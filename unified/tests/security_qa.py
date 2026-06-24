#!/usr/bin/env python3
"""Repeatable security QA harness for the Blakestream multiwallet backend.

Stands up a REAL stack — a throwaway Argon2/AESGCM vault, the orchestrator driving one live
``electrum daemon`` (BLC), and the actual ``api.make_server`` HTTP API — then fires an adversarial
battery at it: auth/host/token gating, body limits, route/JSON fuzzing, per-route input validation
for every /tools route, argv-injection and path-traversal probes, the reveal-seed/export-privkey
vault gate + rate-limit, and a secrets-never-in-argv/world-readable sweep. Negative controls
(no-token->401, foreign Host->403, oversized->413, unknown->404, wrong-pw->401, rate-limit->429)
must always pass.

Run from the repo root (needs the BLC variant workspace + a python with electrum runtime deps):

    .venv/bin/python -m unified.tests.security_qa            # offline (default)
    BLC_SERVER=electrum1.blakestream.io:50002:s \
        .venv/bin/python -m unified.tests.security_qa        # also run online-only cases

Exit code 0 = all passed, 1 = at least one FAIL. Idempotent + self-cleaning (temp datadir).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

# Allow running as a file or a module.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from unified import api, provisioning, vault                       # noqa: E402
from unified.orchestrator import Orchestrator                      # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WSROOT = os.path.join(REPO, "build", "workspaces")
PYBIN = os.environ.get("QA_PYBIN", os.path.join(REPO, ".venv", "bin", "python"))
PORT = int(os.environ.get("QA_API_PORT", "57100"))
RPC_PORT = int(os.environ.get("QA_RPC_PORT", "57231"))
PASSWORD = "qa-pass-123456"
TOKEN = "qa-token-" + "z" * 24
BLC_SERVER = os.environ.get("BLC_SERVER")            # set to a "host:port:s" to enable online cases
ONLINE = bool(BLC_SERVER)


# --------------------------------------------------------------------------------------------------
# tiny test framework
# --------------------------------------------------------------------------------------------------
class Results:
    def __init__(self):
        self.rows = []   # (name, status, detail)  status in PASS/FAIL/SKIP

    def record(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        mark = {"PASS": "\033[32m✓\033[0m", "FAIL": "\033[31m✗\033[0m", "SKIP": "\033[33m∼\033[0m"}[status]
        print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))

    def case(self, name, fn, skip=False, skip_reason=""):
        if skip:
            self.record(name, "SKIP", skip_reason); return
        try:
            fn()
            self.record(name, "PASS")
        except AssertionError as e:
            self.record(name, "FAIL", str(e)[:160])
        except Exception as e:                       # an unexpected crash is itself a failure
            self.record(name, "FAIL", f"{type(e).__name__}: {str(e)[:140]}")

    @property
    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]


# --------------------------------------------------------------------------------------------------
# HTTP helpers — raw enough to send hostile requests the typed client never would
# --------------------------------------------------------------------------------------------------
def raw_request(method, path, body=None, token=TOKEN, host="127.0.0.1", raw_body=None, headers=None):
    """Return (status, parsed_json_or_text). Never raises on HTTP error — returns the code."""
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, (bytes, bytearray)) else str(raw_body).encode()
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None
    h = {"Content-Type": "application/json"}
    if host is not None:
        h["Host"] = host
    if token is not None:
        h["Authorization"] = "Bearer " + token
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            try:
                return r.status, json.loads(txt)
            except ValueError:
                return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def post(path, body, **kw):
    return raw_request("POST", path, body=body, **kw)


def get(path, **kw):
    return raw_request("GET", path, **kw)


# --------------------------------------------------------------------------------------------------
# stack setup / teardown
# --------------------------------------------------------------------------------------------------
class Stack:
    def __init__(self):
        self.datadirs = tempfile.mkdtemp(prefix="qa-sec-")
        self.mnemonic = provisioning.generate_mnemonic()
        self.vault_path = os.path.join(self.datadirs, "vault.enc")
        vault.create_vault(self.vault_path, self.mnemonic, PASSWORD)
        self.orch = Orchestrator(
            python_bin=PYBIN, workspaces_root=WSROOT, datadirs_root=self.datadirs,
            servers={"BLC": BLC_SERVER}, coins=provisioning.load_coins(), ports={"BLC": RPC_PORT})
        self.httpd = None
        self.addr = None
        self.daemon_pid = None

    def up(self):
        wallet_pws, contacts_key = vault.derive_session_keys(self.mnemonic, list(self.orch.daemons))
        self.orch.set_session_keys(wallet_pws, contacts_key)
        self.orch.bring_up("BLC", mnemonic=self.mnemonic, ready_timeout=60)
        self.addr = self.orch.first_address("BLC")
        d = self.orch.daemons["BLC"]
        self.daemon_pid = d.proc.pid if d.proc else None
        self.httpd = api.make_server(self.orch, "127.0.0.1", PORT, vault_path=self.vault_path, token=TOKEN)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.5)

    def down(self):
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.orch.stop_all()
        except Exception:
            pass


# --------------------------------------------------------------------------------------------------
# test groups
# --------------------------------------------------------------------------------------------------
def grp_auth(R, S):
    print("\n[ auth / host / token ]")

    def no_token():
        s, _ = get("/portfolio", token=None)
        assert s == 401, f"no-token expected 401, got {s}"
    R.case("no bearer token -> 401", no_token)

    def wrong_token():
        s, _ = get("/portfolio", token="not-the-token")
        assert s == 401, f"wrong-token expected 401, got {s}"
    R.case("wrong bearer token -> 401", wrong_token)

    def foreign_host():
        s, _ = get("/portfolio", host="evil.example.com")
        assert s == 403, f"foreign Host expected 403, got {s}"
    R.case("foreign Host header -> 403", foreign_host)

    def handshake_tokenless():
        s, r = get("/handshake?nonce=abc", token=None)
        assert s == 200 and isinstance(r, dict) and r.get("proof"), f"handshake should be tokenless 200+proof, got {s} {r}"
    R.case("handshake works without a token", handshake_tokenless)

    def handshake_proof_correct():
        import hmac
        s, r = get("/handshake?nonce=qa-nonce", token=None)
        want = hmac.new(TOKEN.encode(), b"qa-nonce", "sha256").hexdigest()
        assert r.get("proof") == want, "handshake proof does not match HMAC(token, nonce)"
    R.case("handshake proof = HMAC(token,nonce) (client can verify backend identity)", handshake_proof_correct)

    def sensitive_route_needs_token():
        s, _ = post("/tools/BLC/reveal-seed", {"password": PASSWORD}, token=None)
        assert s == 401, f"reveal-seed without token expected 401, got {s}"
    R.case("reveal-seed without token -> 401 (no tokenless secret access)", sensitive_route_needs_token)


def grp_limits(R, S):
    print("\n[ body limits / json / routing ]")

    def oversized_body():
        s, _ = post("/tools/BLC/load-tx", None, raw_body=b'{"tx":"' + b"a" * (70 * 1024) + b'"}')
        assert s == 413, f"oversized body expected 413, got {s}"
    R.case("oversized body (>64KB) -> 413", oversized_body)

    def invalid_json():
        s, _ = post("/tools/BLC/load-tx", None, raw_body=b"{not json")
        assert s == 400, f"invalid json expected 400, got {s}"
    R.case("invalid JSON body -> 400", invalid_json)

    def non_dict_json():
        s, _ = post("/tools/BLC/load-tx", None, raw_body=b"[1,2,3]")
        assert s == 400, f"non-dict json expected 400, got {s}"
    R.case("JSON array body (non-dict) -> 400 (no .get crash)", non_dict_json)

    def unknown_route():
        s, _ = get("/totally/unknown/route")
        assert s == 404, f"unknown route expected 404, got {s}"
    R.case("unknown route -> 404", unknown_route)

    def unknown_tools_sub():
        s, _ = post("/tools/BLC/does-not-exist", {})
        assert s == 404, f"unknown tools sub-route expected 404, got {s}"
    R.case("unknown /tools sub-route -> 404", unknown_tools_sub)

    def unknown_coin():
        s, r = get("/tools/ZZZ/utxos")
        assert s in (404, 500) and s != 200, f"unknown coin should not 200, got {s} {r}"
        assert s == 404, f"unknown coin should be a clean 404 (KeyError), got {s}"
    R.case("unknown coin -> clean 404 (not a 500)", unknown_coin)

    def limit_non_numeric():
        s, r = get("/addresses/BLC?limit=abc")
        assert s == 400, f"non-numeric ?limit expected 400, got {s} {r}"
        assert "invalid literal" not in json.dumps(r), "must not reflect the raw int() error"
    R.case("/addresses ?limit=abc -> clean 400 (not 500, no raw int error)", limit_non_numeric)

    def limit_negative_and_huge():
        s1, _ = get("/addresses/BLC?limit=-5")
        s2, _ = get("/addresses/BLC?limit=99999")
        assert s1 == 200 and s2 == 200, f"negative/huge limit should be clamped to 200, got {s1},{s2}"
    R.case("/addresses ?limit negative/huge -> clamped, 200", limit_negative_and_huge)


def grp_injection(R, S):
    print("\n[ injection / path traversal / type confusion ]")

    def coin_path_traversal():
        # a coin segment of ".." or with slashes must not escape into the filesystem / 200
        for bad in ["..", "%2e%2e", "etc"]:
            s, r = get(f"/tools/{bad}/utxos")
            assert s != 200, f"coin '{bad}' unexpectedly 200: {r}"
    R.case("coin path-traversal ('..','etc') -> never 200", coin_path_traversal)

    def dashy_address_signmessage():
        # a value starting with '-' must not be interpreted as a CLI flag (we use _drpc named params);
        # it should be a clean daemon rejection, never a hang/crash/odd success.
        s, r = post("/tools/BLC/sign-message", {"address": "-rf", "message": "x"})
        assert s == 400, f"dashy address should be a clean 400, got {s} {r}"
    R.case("address starting with '-' (sign) -> clean 400, no argv injection", dashy_address_signmessage)

    def dashy_txid_fetch():
        s, r = post("/tools/BLC/fetch-tx", {"txid": "--help"})
        assert s == 400, f"dashy txid should be clean 400 (invalid txid), got {s} {r}"
    R.case("txid '--help' (fetch) -> clean 400", dashy_txid_fetch)

    def type_confusion_loadtx():
        # tx as a list/number/dict must not 500 — must be a clean 400.
        for val in [[1, 2], 12345, {"x": 1}, True]:
            s, r = post("/tools/BLC/load-tx", {"tx": val})
            assert s == 400, f"load-tx tx={val!r} expected 400, got {s} {r}"
    R.case("load-tx tx as list/number/dict/bool -> 400 (no 500)", type_confusion_loadtx)

    def type_confusion_paytomany():
        for outs in [["notalist"], [["addr"]], [["a", "b", "c"]], [[1, 2]], "string", 5]:
            s, r = post("/tools/BLC/pay-to-many", {"outputs": outs})
            assert s == 400, f"pay-to-many outputs={outs!r} expected 400, got {s} {r}"
    R.case("pay-to-many malformed outputs -> 400 (no 500)", type_confusion_paytomany)

    def freeze_foreign_address():
        # freezing an address that isn't ours must be SAFE: it must never falsely claim success
        # (ok:true). A safe no-op (200 ok:false) or a clean 400 are both acceptable; a crash/500 or a
        # lying ok:true are not.
        s, r = post("/tools/BLC/freeze-address", {"address": "1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "frozen": True})
        assert s in (200, 400), f"foreign-address freeze should be a clean 200/400, got {s} {r}"
        if s == 200:
            assert r.get("ok") is not True, f"freeze of a non-wallet address must NOT claim ok:true, got {r}"
    R.case("freeze a non-wallet address -> safe (no false ok:true)", freeze_foreign_address)


def grp_tools_validation(R, S):
    print("\n[ tools routes: required-field validation ]")
    required = [
        ("load-tx", {}, "tx"),
        ("fetch-tx", {}, "txid"),
        ("broadcast", {}, "tx"),
        ("sign-message", {"message": "x"}, "address"),
        ("verify-message", {"message": "x"}, "address/signature"),
        ("freeze-address", {"frozen": True}, "address"),
        ("encrypt-message", {"message": "x"}, "key"),
        ("decrypt-message", {"key": "x"}, "encrypted"),
        ("pay-to-many", {}, "outputs"),
        ("sweep", {"destination": "x"}, "privkey"),
        ("bump-fee", {"new_feerate": "5"}, "tx"),
        ("export-privkey", {"address": "x"}, "password"),
        ("reveal-seed", {}, "password"),
    ]
    for sub, body, missing in required:
        def chk(sub=sub, body=body):
            s, r = post(f"/tools/BLC/{sub}", body)
            assert s == 400, f"{sub} missing field expected 400, got {s} {r}"
        R.case(f"{sub}: missing {missing} -> 400", chk)


def grp_reveal_gate(R, S):
    print("\n[ reveal-seed / export-privkey vault gate ]")

    def seed_wrong_pw():
        s, _ = post("/tools/BLC/reveal-seed", {"password": "WRONG-PASS"})
        assert s == 401, f"reveal-seed wrong pw expected 401, got {s}"
    R.case("reveal-seed wrong password -> 401", seed_wrong_pw)

    def seed_right_pw():
        s, r = post("/tools/BLC/reveal-seed", {"password": PASSWORD})
        assert s == 200 and r.get("seed") == S.mnemonic, "reveal-seed right pw must return the vault mnemonic"
    R.case("reveal-seed right password -> the vault seed", seed_right_pw)

    def privkey_wrong_pw():
        s, _ = post("/tools/BLC/export-privkey", {"password": "WRONG", "address": S.addr})
        assert s == 401, f"export-privkey wrong pw expected 401, got {s}"
    R.case("export-privkey wrong password -> 401", privkey_wrong_pw)

    def privkey_right_pw():
        s, r = post("/tools/BLC/export-privkey", {"password": PASSWORD, "address": S.addr})
        assert s == 200 and isinstance(r.get("privkey"), str) and len(r["privkey"]) > 40, "export-privkey must return a WIF"
    R.case("export-privkey right password -> WIF", privkey_right_pw)

    def import_removed():
        s, _ = post("/tools/BLC/import-privkey", {"privkey": "x"})
        assert s == 404, f"import-privkey should be removed (404), got {s}"
    R.case("import-privkey removed -> 404", import_removed)

    def correct_reveals_dont_lock():
        # CORRECT-password reveals must NOT consume the brute-force budget -> a user can reveal across
        # many coins without a self-inflicted 429. (Run while the failure budget is still well under MAX.)
        codes = [post("/tools/BLC/reveal-seed", {"password": PASSWORD})[0] for _ in range(13)]
        assert all(c == 200 for c in codes), f"correct-password reveals must all be 200, got {codes}"
    R.case("13 correct-password reveals -> all 200 (no self-lockout)", correct_reveals_dont_lock)

    def rate_limit_shared_counter():
        # The reveal-seed AND export-privkey routes share ONE 10/60s counter — alternating between
        # them must NOT bypass the limit. Within 12 mixed wrong-password attempts a 429 must appear.
        codes = []
        for i in range(12):
            if i % 2 == 0:
                codes.append(post("/tools/BLC/reveal-seed", {"password": "WRONG"})[0])
            else:
                codes.append(post("/tools/BLC/export-privkey", {"password": "WRONG", "address": S.addr})[0])
        assert codes.count(429) >= 1, f"expected a 429 within 12 mixed attempts, got {codes}"
        assert codes[0] in (401, 429), f"first attempt should be 401 (or 429 if window already spent), got {codes[0]}"
    R.case("reveal rate-limit shared across routes (no bypass by varying route) -> 429", rate_limit_shared_counter)

    def unlock_shares_cap():
        # /setup/unlock verifies the SAME vault password, so it must honour the SAME budget — otherwise
        # the reveal cap is bypassable by grinding /setup/unlock. After the reveal budget is exhausted
        # (previous test), an unlock attempt is refused with 429 before it even checks the password.
        s, _ = post("/setup/unlock", {"password": PASSWORD})
        assert s == 429, f"/setup/unlock should share the reveal brute-force cap (429), got {s}"
    R.case("/setup/unlock shares the reveal brute-force cap -> 429", unlock_shares_cap)


def grp_secrets(R, S):
    print("\n[ secret leakage: argv / perms / logs ]")

    def perform_secret_ops():
        # exercise every secret-bearing path so a leak would have happened by now
        post("/tools/BLC/reveal-seed", {"password": PASSWORD})
        post("/tools/BLC/export-privkey", {"password": PASSWORD, "address": S.addr})
        post("/tools/BLC/sign-message", {"address": S.addr, "message": "qa"})
        post("/tools/BLC/decrypt-message", {"key": S.addr, "encrypted": "BIE1bogus"})

    def secrets_not_in_argv():
        perform_secret_ops()
        time.sleep(0.3)
        # scan EVERY process's cmdline for the password / seed words / the exported WIF
        s, r = post("/tools/BLC/export-privkey", {"password": PASSWORD, "address": S.addr})
        wif = r.get("privkey") if isinstance(r, dict) else None
        needles = [PASSWORD, S.mnemonic, S.mnemonic.split()[0] + " " + S.mnemonic.split()[1]]
        if wif:
            needles.append(wif)
        hits = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            for n in needles:
                if n and n in cmd:
                    hits.append((pid, n[:12]))
        assert not hits, f"SECRET FOUND IN ARGV: {hits}"
    R.case("password/seed/WIF never appear in any process argv", secrets_not_in_argv)

    def datadir_perms():
        mode = oct(os.stat(S.datadirs).st_mode & 0o777)
        assert mode == "0o700", f"datadir root should be 0700, is {mode}"
        vmode = oct(os.stat(S.vault_path).st_mode & 0o777)
        assert vmode in ("0o600", "0o400"), f"vault file should be 0600, is {vmode}"
    R.case("datadir root 0700 + vault file 0600 (not world-readable)", datadir_perms)

    def config_perms_no_world_read():
        cfg = os.path.join(S.datadirs, "blc", "config")
        if not os.path.exists(cfg):
            cfg = os.path.join(S.orch.daemons["BLC"].datadir, "config")
        assert os.path.exists(cfg), "coin config not found"
        mode = os.stat(cfg).st_mode & 0o777
        assert not (mode & 0o077), f"coin config (holds rpcpassword) is group/other-readable: {oct(mode)}"
    R.case("coin config (rpcpassword) is owner-only", config_perms_no_world_read)

    def logs_no_secrets():
        # any log files under the datadir must not contain the password or seed
        found = []
        for root, _dirs, files in os.walk(S.datadirs):
            for fn in files:
                if not fn.endswith((".log", ".txt")):
                    continue
                p = os.path.join(root, fn)
                try:
                    with open(p, "r", errors="replace") as f:
                        blob = f.read()
                except OSError:
                    continue
                if PASSWORD in blob or S.mnemonic in blob:
                    found.append(p)
        assert not found, f"secret found in log file(s): {found}"
    R.case("no log file under the datadir contains the password/seed", logs_no_secrets)


def grp_adv_amounts(R, S):
    print("\n[ pay-to-many: hostile amounts / sizes ]")

    def negative_amount():
        s, r = post("/tools/BLC/pay-to-many", {"outputs": [[S.addr, "-1"]]})
        assert s == 400, f"negative amount expected clean 400, got {s} {r}"
    R.case("pay-to-many negative amount -> clean 400 (no 500)", negative_amount)

    def nonnumeric_amount():
        s, r = post("/tools/BLC/pay-to-many", {"outputs": [[S.addr, "abc"]]})
        assert s == 400, f"non-numeric amount expected clean 400, got {s} {r}"
    R.case("pay-to-many non-numeric amount -> clean 400", nonnumeric_amount)

    def dust_amount():
        s, r = post("/tools/BLC/pay-to-many", {"outputs": [[S.addr, "0.00000001"]]})
        assert s == 400, f"dust amount expected clean 400 (insufficient/ dust), got {s} {r}"
    R.case("pay-to-many dust amount -> clean 400", dust_amount)

    def dashy_paytomany_address():
        s, r = post("/tools/BLC/pay-to-many", {"outputs": [["-rf", "1"]]})
        assert s == 400, f"leading-'-' address expected clean 400, got {s} {r}"
    R.case("pay-to-many address starting with '-' -> clean 400", dashy_paytomany_address)

    def many_outputs():
        # ~1000 outputs (stays under the 64KB body cap) must not 500/hang — a clean error.
        outs = [[S.addr, "0.001"] for _ in range(900)]
        s, r = post("/tools/BLC/pay-to-many", {"outputs": outs})
        assert s == 400, f"900 outputs expected clean 400, got {s} {str(r)[:80]}"
    R.case("pay-to-many ~900 outputs -> clean 400 (no 500/hang)", many_outputs)


def grp_error_hygiene(R, S):
    print("\n[ error hygiene: no traceback / secret echo in error bodies ]")

    def no_traceback_leak():
        # an internal daemon error must not return a Python traceback / source paths to the client.
        probes = [
            ("POST", "/tools/BLC/sign-message", {"address": S.addr + "x", "message": "y"}),
            ("POST", "/tools/BLC/decrypt-message", {"key": S.addr, "encrypted": "BIE1notreal"}),
            ("POST", "/tools/BLC/bump-fee", {"tx": "00" * 32, "new_feerate": "5"}),
        ]
        for method, path, body in probes:
            s, r = raw_request(method, path, body=body)
            blob = json.dumps(r)
            for bad in ("Traceback (most recent call last)", "/home/", "File \"", ".py\", line"):
                assert bad not in blob, f"{path} leaked '{bad}' in error: {blob[:160]}"
    R.case("internal errors expose no traceback / source paths", no_traceback_leak)

    def no_secret_echo():
        # error bodies must never echo the password / seed
        s, r = post("/tools/BLC/reveal-seed", {"password": "WRONGPW"})
        blob = json.dumps(r)
        assert "WRONGPW" not in blob, f"error echoed the submitted password: {blob[:120]}"
        assert S.mnemonic.split()[0] not in blob, "error body leaked a seed word"
    R.case("error bodies don't echo the submitted password / seed", no_secret_echo)


def grp_query_string(R, S):
    print("\n[ secrets must be body-only, never query string ]")

    def pw_in_query_ignored():
        # password supplied ONLY in the query string must be ignored -> 400 (not accepted as auth)
        s, r = post("/tools/BLC/reveal-seed?password=" + PASSWORD, {})
        assert s == 400, f"query-string password should be ignored (400), got {s} {r}"
    R.case("reveal-seed password in query string -> ignored (400)", pw_in_query_ignored)


def grp_reaper(R, S):
    print("\n[ port-owner reaper safety ]")

    def reaper_only_kills_port_owner():
        # The reaper kills processes that own the API port AND look like our supervisor. A decoy with
        # a matching argv ("electrum-backend ... --serve ... multi") that does NOT own the port must
        # survive — i.e. the reaper targets the PORT OWNER, not a name match.
        import subprocess
        decoy = subprocess.Popen(
            ["bash", "-c", 'exec -a electrum-backend python3 -c "import time;time.sleep(30)" --serve multi'])
        try:
            time.sleep(0.5)
            assert decoy.poll() is None, "decoy failed to start"
            # reap on an UNUSED port the decoy is not listening on
            api._reap_foreign_supervisor(59999)
            time.sleep(0.3)
            assert decoy.poll() is None, "reaper KILLED a name-matching process that did not own the port"
        finally:
            decoy.terminate()
            try:
                decoy.wait(timeout=5)
            except Exception:
                decoy.kill()
    R.case("reaper does NOT kill a name-matching non-port-owner", reaper_only_kills_port_owner)


def grp_renderer_static(R, S):
    print("\n[ renderer: static XSS / tabnabbing checks ]")
    src = os.path.join(REPO, "unified", "desktop", "src")

    def no_dangerous_html():
        hits = []
        for root, _d, files in os.walk(src):
            for fn in files:
                if not fn.endswith((".ts", ".tsx")):
                    continue
                p = os.path.join(root, fn)
                with open(p, errors="replace") as f:
                    blob = f.read()
                if "dangerouslySetInnerHTML" in blob:
                    hits.append(p)
                if ".innerHTML" in blob or "eval(" in blob:
                    hits.append(p + " (innerHTML/eval)")
        assert not hits, f"renderer uses raw-HTML/eval sinks (XSS risk): {hits}"
    R.case("no dangerouslySetInnerHTML / innerHTML / eval in renderer", no_dangerous_html)

    def external_links_safe():
        # every target=_blank anchor must carry rel="noreferrer" (reverse-tabnabbing + referrer leak)
        bad = []
        for root, _d, files in os.walk(src):
            for fn in files:
                if not fn.endswith(".tsx"):
                    continue
                p = os.path.join(root, fn)
                with open(p, errors="replace") as f:
                    blob = f.read()
                import re
                for m in re.finditer(r"target=\{?['\"]_blank['\"]\}?", blob):
                    window = blob[m.start():m.start() + 400]
                    if "noreferrer" not in window and "noopener" not in window:
                        bad.append(f"{os.path.basename(p)} @ {m.start()}")
        assert not bad, f"target=_blank without rel=noreferrer/noopener: {bad}"
    R.case("all target=_blank links carry rel=noreferrer/noopener", external_links_safe)


def grp_vault(R, S):
    print("\n[ vault: corrupt / version handling ]")

    def bad_version_clean():
        # a vault with an unsupported version must raise a clean ValueError (the API maps -> 400),
        # never an uncaught crash -> 500.
        import json as _json
        bad = os.path.join(S.datadirs, "vault-badver.enc")
        vault.create_vault(bad, S.mnemonic, PASSWORD)
        blob = _json.load(open(bad))
        blob["version"] = 999
        _json.dump(blob, open(bad, "w"))
        try:
            vault.unlock_vault(bad, PASSWORD)
            raise AssertionError("bad-version vault should not unlock")
        except vault.BadPassword:
            raise AssertionError("bad version surfaced as BadPassword (should be a distinct ValueError)")
        except ValueError:
            pass   # expected -> the route maps this to a clean 400
        finally:
            try:
                os.remove(bad)
            except OSError:
                pass
    R.case("unsupported vault version -> clean ValueError (-> 400, not 500)", bad_version_clean)

    def corrupt_vault_badpassword():
        bad = os.path.join(S.datadirs, "vault-corrupt.enc")
        with open(bad, "w") as f:
            f.write("{ not valid json")
        try:
            vault.unlock_vault(bad, PASSWORD)
            raise AssertionError("corrupt vault should not unlock")
        except vault.BadPassword:
            pass   # expected
        finally:
            try:
                os.remove(bad)
            except OSError:
                pass
    R.case("corrupt vault -> BadPassword (uniform contract)", corrupt_vault_badpassword)


def grp_online(R, S):
    print("\n[ online (ElectrumX) — only with BLC_SERVER ]")

    def connects():
        for _ in range(40):
            if S.orch.getinfo("BLC").get("connected"):
                return
            time.sleep(1)
        raise AssertionError("daemon did not connect to ElectrumX")
    R.case("daemon connects to live ElectrumX", connects, skip=not ONLINE, skip_reason="set BLC_SERVER to enable")

    def fetch_unknown_clean():
        s, r = post("/tools/BLC/fetch-tx", {"txid": "de" * 32})
        assert s == 400 and "not known" in json.dumps(r).lower(), f"unknown txid should be a clean 400, got {s} {r}"
    R.case("fetch-tx unknown txid -> clean 400 (network round-trip)", fetch_unknown_clean,
           skip=not ONLINE, skip_reason="set BLC_SERVER to enable")


# --------------------------------------------------------------------------------------------------
def main():
    print(f"=== multiwallet security QA  (online={ONLINE})  port={PORT} rpc={RPC_PORT} ===")
    R = Results()
    S = Stack()
    try:
        S.up()
        print(f"stack up: BLC daemon pid={S.daemon_pid}, address={S.addr}")
        grp_auth(R, S)
        grp_limits(R, S)
        grp_injection(R, S)
        grp_tools_validation(R, S)
        grp_adv_amounts(R, S)
        grp_error_hygiene(R, S)
        grp_query_string(R, S)
        grp_reaper(R, S)
        grp_renderer_static(R, S)
        grp_vault(R, S)
        grp_secrets(R, S)        # before the reveal rate-limit burns the window
        grp_online(R, S)
        grp_reveal_gate(R, S)    # last: its rate-limit test exhausts the reveal window
    finally:
        S.down()

    npass = sum(1 for r in R.rows if r[1] == "PASS")
    nfail = len(R.failed)
    nskip = sum(1 for r in R.rows if r[1] == "SKIP")
    print(f"\n=== {npass} passed, {nfail} failed, {nskip} skipped ===")
    if R.failed:
        print("FAILURES:")
        for name, _st, detail in R.failed:
            print(f"  ✗ {name} — {detail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
