"""Adversarial edge-case suite for the security-critical pure functions:
the seed vault (unified/vault.py) and key derivation (unified/provisioning.py).

Hostile / boundary inputs only — corrupted vaults, KDF-param rollback, wrong
passwords, malformed mnemonics, cross-coin address confusion, entropy sanity. No
daemons, no network; safe to run anywhere the deps import.

    PYTHONPATH=<repo-or-staging>:<an-electrum-workspace> python -m unified.tests.edge_cases_security
"""

import json
import os
import stat
import tempfile

from unified import vault, provisioning

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def _new_vault(mnemonic="abandon abandon abandon abandon abandon abandon "
                        "abandon abandon abandon abandon abandon about",
               password="correct horse battery staple"):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "vault.enc")
    vault.create_vault(path, mnemonic, password)
    return path, mnemonic, password


# --------------------------------------------------------------------------- #
# VAULT
# --------------------------------------------------------------------------- #

@case("vault: round-trip create/unlock returns the exact mnemonic")
def _():
    path, m, pw = _new_vault()
    assert vault.unlock_vault(path, pw) == m


@case("vault: wrong password raises BadPassword (not a crash, no plaintext)")
def _():
    path, m, pw = _new_vault()
    try:
        vault.unlock_vault(path, pw + "x")
        assert False, "wrong password did not raise"
    except vault.BadPassword:
        pass


@case("vault: flipping one ciphertext byte -> BadPassword (GCM auth)")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    ct = bytearray(vault._unb64(blob["ciphertext"]))
    ct[0] ^= 0x01
    blob["ciphertext"] = vault._b64(bytes(ct))
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "tampered ciphertext decrypted"
    except vault.BadPassword:
        pass


@case("vault: tampering the salt -> BadPassword (key + AAD both change)")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    salt = bytearray(vault._unb64(blob["salt"]))
    salt[0] ^= 0x01
    blob["salt"] = vault._b64(bytes(salt))
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "tampered salt decrypted"
    except vault.BadPassword:
        pass


@case("vault: KDF-param downgrade (lower memory_cost/time_cost) -> BadPassword")
def _():
    # A tampered vault must not be able to weaken the work factor. unlock validates
    # the stored params against a floor and rejects anything below it.
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["memory_cost"] = 8 * 1024  # below the 64 MiB floor
    blob["time_cost"] = 1
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "downgraded KDF params accepted"
    except vault.BadPassword:
        pass


@case("vault: absurd memory_cost (DoS) -> BadPassword, not OOM")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["memory_cost"] = 64 * 1024 * 1024  # 64 GiB — would OOM if honored
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "absurd memory_cost accepted"
    except vault.BadPassword:
        pass


@case("vault: tampered kdf name -> BadPassword")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["kdf"] = "pbkdf2"
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "tampered kdf name accepted"
    except vault.BadPassword:
        pass


@case("vault: structurally corrupt field (bad base64 salt) -> BadPassword (not raw decode error)")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["salt"] = "!!!not base64!!!"
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "corrupt salt accepted"
    except vault.BadPassword:
        pass


@case("vault: truncated / non-JSON file -> BadPassword (clean error, not a 500 decode crash)")
def _():
    d = tempfile.mkdtemp()
    for content in (b"{not json at all", b"", b"\xff\xfe\x00garbage", b'{"version":1'):
        path = os.path.join(d, f"v_{abs(hash(content))}.enc")
        open(path, "wb").write(content)
        try:
            vault.unlock_vault(path, "pw12345678")
            assert False, f"corrupt vault {content!r} accepted"
        except vault.BadPassword:
            pass


@case("vault: top-level JSON is not an object (list/null) -> BadPassword (no uncaught AttributeError)")
def _():
    d = tempfile.mkdtemp()
    for content in ("[]", "null", '"x"', "123"):
        path = os.path.join(d, f"v_{abs(hash(content))}.enc")
        open(path, "w").write(content)
        try:
            vault.unlock_vault(path, "pw12345678")
            assert False, f"non-object vault {content!r} accepted"
        except vault.BadPassword:
            pass


@case("vault: float version 1.0 does NOT slip past the version check -> ValueError")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["version"] = 1.0   # 1.0 == 1 in Python; must still be rejected (type strictness)
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "float version 1.0 accepted"
    except ValueError:
        pass


@case("vault: string/float KDF params are coerced, never reach argon2 raw (no uncaught TypeError)")
def _():
    # String/float params that coerce to the same in-range int must unlock (with the
    # right password), NOT raise an uncaught TypeError from argon2.
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["time_cost"] = str(blob["time_cost"])      # "3"
    blob["memory_cost"] = float(blob["memory_cost"])  # 65536.0
    json.dump(blob, open(path, "w"))
    assert vault.unlock_vault(path, pw) == m
    # a non-numeric param is corrupt -> BadPassword (caught), not a raw crash
    blob["parallelism"] = "abc"
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False, "non-numeric parallelism accepted"
    except vault.BadPassword:
        pass


@case("vault: truncated/empty JSON -> exception, never silent success")
def _():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "v.enc")
    open(path, "w").write("{ this is not json")
    raised = False
    try:
        vault.unlock_vault(path, "pw")
    except Exception:
        raised = True
    assert raised


@case("vault: missing field -> exception (KeyError/ValueError), not crash-with-secret")
def _():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "v.enc")
    json.dump({"version": 1, "salt": "AAAA"}, open(path, "w"))  # missing nonce/ciphertext/params
    raised = False
    try:
        vault.unlock_vault(path, "pw")
    except Exception:
        raised = True
    assert raised


@case("vault: unsupported version -> ValueError")
def _():
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    blob["version"] = 999
    json.dump(blob, open(path, "w"))
    try:
        vault.unlock_vault(path, pw)
        assert False
    except ValueError:
        pass


@case("vault: file mode is 0600 (owner-only)")
def _():
    path, m, pw = _new_vault()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"vault perms {oct(mode)} != 0600"


@case("vault: concurrent create_vault to one path is race-safe (unique 0600 tmp, no FileExistsError/leftovers)")
def _():
    import threading
    d = tempfile.mkdtemp()
    path = os.path.join(d, "vault.enc")
    errors = []

    def mk(i):
        try:
            vault.create_vault(path, f"seed-{i} " + " ".join(["abandon"] * 8) + " about", "pw12345678")
        except Exception as e:  # noqa
            errors.append(repr(e))

    ts = [threading.Thread(target=mk, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"concurrent create_vault raised: {errors}"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    leftovers = [f for f in os.listdir(d) if f.startswith(".vault-")]
    assert not leftovers, f"leftover temp files after concurrent writes: {leftovers}"


@case("vault: long + unicode password round-trips")
def _():
    path, m, pw = _new_vault(password="🔐" * 200 + "Ω-pässwörd-" + "x" * 5000)
    assert vault.unlock_vault(path, "🔐" * 200 + "Ω-pässwörd-" + "x" * 5000) == m


@case("vault: plaintext mnemonic does NOT appear in the on-disk vault bytes")
def _():
    secret = "ozone drill grab fiber curtain grace pudding thank cruise elder eight picnic"
    d = tempfile.mkdtemp()
    path = os.path.join(d, "v.enc")
    vault.create_vault(path, secret, "pw")
    raw = open(path, "rb").read()
    for word in secret.split():
        assert word.encode() not in raw, f"word {word!r} leaked into vault file"


@case("vault: AAD is load-bearing (decrypt with wrong AAD fails)")
def _():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    path, m, pw = _new_vault()
    blob = json.load(open(path))
    salt = vault._unb64(blob["salt"])
    key = vault._derive_key(pw, salt, time_cost=blob["time_cost"],
                            memory_cost=blob["memory_cost"], parallelism=blob["parallelism"])
    nonce = vault._unb64(blob["nonce"]); ct = vault._unb64(blob["ciphertext"])
    # correct AAD works; wrong AAD (missing salt) must fail
    assert AESGCM(key).decrypt(nonce, ct, vault.AAD + salt).decode() == m
    try:
        AESGCM(key).decrypt(nonce, ct, vault.AAD)  # AAD without salt
        assert False, "decrypt succeeded with wrong AAD"
    except InvalidTag:
        pass


# --------------------------------------------------------------------------- #
# PROVISIONING / KEY DERIVATION
# --------------------------------------------------------------------------- #

@case("bip39: bad checksum rejected")
def _():
    bad = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon"
    assert provisioning.is_valid_bip39(bad) is False


@case("bip39: unknown word rejected")
def _():
    bad = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon zzzzzz"
    assert provisioning.is_valid_bip39(bad) is False


@case("bip39: wrong word count rejected (11 and 13 words)")
def _():
    eleven = " ".join(["abandon"] * 11)
    thirteen = " ".join(["abandon"] * 12 + ["about"])
    assert provisioning.is_valid_bip39(eleven) is False
    assert provisioning.is_valid_bip39(thirteen) is False


@case("derive: invalid mnemonic raises (does not silently derive garbage)")
def _():
    try:
        provisioning.derive_all("not a real mnemonic at all friend")
        assert False, "derive_all accepted an invalid mnemonic"
    except ValueError:
        pass


@case("entropy: generate_mnemonic x64 are all valid BIP39 and unique")
def _():
    seen = set()
    for _i in range(64):
        m = provisioning.generate_mnemonic()
        assert provisioning.is_valid_bip39(m), f"generated invalid mnemonic: {m}"
        seen.add(m)
    assert len(seen) == 64, "generate_mnemonic produced a duplicate (entropy bug)"


@case("derive: deterministic — same seed -> same addresses")
def _():
    a = provisioning.derive_all(provisioning.generate_mnemonic.__defaults__ and
                                "abandon abandon abandon abandon abandon abandon "
                                "abandon abandon abandon abandon abandon about")
    b = provisioning.derive_all("abandon abandon abandon abandon abandon abandon "
                                "abandon abandon abandon abandon abandon about")
    for t in a:
        assert a[t].receive == b[t].receive and a[t].change == b[t].change


@case("derive: BIP39 passphrase changes the addresses (25th word matters)")
def _():
    m = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    base = provisioning.derive_all(m)["BLC"].receive[0]
    withpass = provisioning.derive_all(m, "trezor")["BLC"].receive[0]
    assert base != withpass


@case("cross-coin: a BLC address does NOT decode under another coin's HRP")
def _():
    from electrum import segwit_addr
    m = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    coins = provisioning.load_coins()
    blc_addr = provisioning.derive_all(m)["BLC"].receive[0]  # blc1...
    # decoding under BLC hrp works; under any other coin's hrp it must fail (None)
    ok_wit, ok_data = segwit_addr.decode_segwit_address("blc", blc_addr)
    assert ok_data is not None, "BLC address failed to decode under its own hrp"
    for t, c in coins.items():
        if t == "BLC":
            continue
        wit, data = segwit_addr.decode_segwit_address(c["segwit_hrp"], blc_addr)
        assert data is None, f"BLC address wrongly decoded under {t} hrp {c['segwit_hrp']}"


# --------------------------------------------------------------------------- #
# API (live in-process server, daemons stubbed — exercises the real handler/auth)
# --------------------------------------------------------------------------- #

class _StubOrch:
    """Minimal orchestrator stand-in so the HTTP handler runs without daemons.
    Mirrors the real orchestrator's KeyError-on-unknown-coin lookup so the handler's
    404 path is exercised faithfully."""
    def __init__(self):
        self.daemons = {"BLC": type("D", (), {"rpc_port": 57101})()}
        self.coins = {"BLC": {"coin_name": "Blakecoin", "coin_type": 10, "segwit_hrp": "blc"}}
        self.provisioned = []
    def portfolio(self): return {"total": {"value_usd": None}, "coins": {}}
    def all_provisioned(self): return bool(self.provisioned)
    def provision_all(self, mnemonic): self.provisioned.append(mnemonic)
    def startup_status(self): return {}
    def first_address(self, c): return "blc1qqqq" if c in self.coins else self.coins[c]  # KeyError on unknown
    def receive_address(self, c): return "blc1qqqq" if c in self.coins else self.coins[c]
    def can_send(self, c): return False if c in self.coins else self.coins[c]


def _serve():
    """Start the real api server with a token, on an ephemeral port. Returns (base, token, vault_path, httpd)."""
    import threading
    from http.server import ThreadingHTTPServer
    from unified import api
    token = "test-token-deadbeef"
    d = tempfile.mkdtemp()
    vpath = os.path.join(d, "vault.enc")
    handler = api.make_handler(_StubOrch(), vault_path=vpath, token=token)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    return base, token, vpath, httpd


def _req(base, method, path, *, token=None, host=None, body=None, raw_body=None):
    import urllib.request
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if host is not None:
        headers["Host"] = host
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@case("api: missing/wrong bearer token -> 401")
def _():
    base, token, vpath, httpd = _serve()
    try:
        assert _req(base, "GET", "/health")[0] == 401          # no token
        assert _req(base, "GET", "/health", token="nope")[0] == 401
        assert _req(base, "GET", "/health", token=token)[0] == 200
    finally:
        httpd.shutdown()


@case("api: non-loopback Host header -> 403 (DNS-rebind defence)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        assert _req(base, "GET", "/health", token=token, host="evil.example.com")[0] == 403
        assert _req(base, "GET", "/health", token=token, host="127.0.0.1")[0] == 200
    finally:
        httpd.shutdown()


@case("api: Host-header matrix — only loopback names pass (DNS-rebind hardening)")
def _():
    import socket
    base, token, vpath, httpd = _serve()
    port = httpd.server_address[1]

    def raw(host_line):
        # host_line=None omits the Host header entirely
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        req = "GET /health HTTP/1.1\r\n"
        if host_line is not None:
            req += f"Host: {host_line}\r\n"
        req += f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode())
        resp = s.recv(120).decode(errors="replace")
        s.close()
        return resp.split("\r\n")[0]

    try:
        # must PASS (200): loopback names, with/without port, bracketed + bare ipv6 loopback
        for h in ("127.0.0.1", "localhost", "127.0.0.1:9999", "[::1]", "[::1]:8080", "::1"):
            assert "200" in raw(h), f"loopback Host {h!r} wrongly rejected: {raw(h)}"
        # must be REJECTED (403): the bypasses the red-team found + classic rebind hosts
        for h in (":evil.com", ":8080", "::evil", "evil.com", "127.0.0.1.evil.com",
                  "127.0.0.1.", "0.0.0.0", "169.254.169.254", "127.0.0.2", "", None):
            assert "403" in raw(h), f"non-loopback Host {h!r} wrongly passed: {raw(h)}"
    finally:
        httpd.shutdown()


@case("api: oversized POST body -> 413 (before processing)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        big = b'{"password":"' + b'A' * (70 * 1024) + b'"}'
        code, _b = _req(base, "POST", "/setup/create", token=token, raw_body=big)
        assert code == 413, f"expected 413, got {code}"
    finally:
        httpd.shutdown()


@case("api: invalid-JSON POST body -> 400")
def _():
    base, token, vpath, httpd = _serve()
    try:
        code, _b = _req(base, "POST", "/setup/create", token=token, raw_body=b"{not json")
        assert code == 400
    finally:
        httpd.shutdown()


@case("api: non-dict JSON body (list/number/string/bool) -> 400 (no AttributeError 500)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        for body in (b"[]", b"123", b'"x"', b"true", b"null"):
            code, _b = _req(base, "POST", "/setup/create", token=token, raw_body=body)
            assert code == 400, f"body {body!r} -> {code} (expected 400)"
    finally:
        httpd.shutdown()


@case("api: restore rejects an invalid BIP39 mnemonic -> 400")
def _():
    base, token, vpath, httpd = _serve()
    try:
        code, b = _req(base, "POST", "/setup/restore", token=token,
                       body={"password": "pw12345678", "mnemonic": "not valid words here"})
        assert code == 400, f"expected 400, got {code}: {b}"
    finally:
        httpd.shutdown()


@case("api: create then second create -> 409 (no second seed minted over an existing vault)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        # create a vault on disk first (so vault_exists() is true), then a 2nd create must 409
        vault.create_vault(vpath, "abandon abandon abandon abandon abandon abandon "
                                  "abandon abandon abandon abandon abandon about", "pw12345678")
        code, b = _req(base, "POST", "/setup/create", token=token, body={"password": "pw12345678"})
        assert code == 409, f"expected 409, got {code}: {b}"
    finally:
        httpd.shutdown()


@case("api: path-param ticker cannot traverse / inject (unknown coin -> 4xx, not 500/crash)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        for p in ("/address/..%2f..%2fetc", "/getinfo/;rm", "/receive/ZZZ"):
            code, _b = _req(base, "GET", p, token=token)
            assert code in (400, 404, 500), f"{p} -> {code}"
            assert code != 200
    finally:
        httpd.shutdown()


@case("api: create with a too-short password -> 400 (no vault minted)")
def _():
    base, token, vpath, httpd = _serve()
    try:
        code, b = _req(base, "POST", "/setup/create", token=token, body={"password": "short"})
        assert code == 400, f"expected 400, got {code}: {b}"
        assert not vault.vault_exists(vpath), "a vault was created despite the weak password"
    finally:
        httpd.shutdown()


@case("api: non-numeric Content-Length -> 400 (no uncaught crash)")
def _():
    # urllib won't send a bogus Content-Length, so hit the socket directly.
    import socket
    base, token, vpath, httpd = _serve()
    port = httpd.server_address[1]
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.sendall((f"POST /setup/create HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                   f"Authorization: Bearer {token}\r\nContent-Length: abc\r\n\r\n").encode())
        resp = s.recv(200).decode(errors="replace")
        s.close()
        assert "400" in resp.split("\r\n")[0], f"expected 400 status, got: {resp.splitlines()[:1]}"
    finally:
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# SEND cross-coin guard (real Orchestrator; needs the variant workspaces)
# --------------------------------------------------------------------------- #

@case("send: cross-coin destination rejected; same-coin bech32 passes the address guard")
def _():
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    import sys
    from unified.orchestrator import Orchestrator
    # BLC marked online so send() reaches the address guard (the six coins share base58
    # version bytes, so the bech32 HRP is the only cross-coin discriminator).
    orch = Orchestrator(python_bin=sys.executable, workspaces_root=wsroot,
                        datadirs_root=tempfile.mkdtemp(), servers={"BLC": "x:50002:s"})
    # a UMO / BBTC address must be rejected as the wrong coin
    for wrong, name in (("umo1qnk09lsphnthwcyhr6km63ug5vzhweh9aksgup3", "UMO"),
                        ("bbtc1qlyg3wuu3zyw85ulz2my7wqh2rndtrufz2g4jdp", "BBTC")):
        try:
            orch.send("BLC", wrong, "1.0")
            assert False, f"cross-coin send to {name} address was not rejected"
        except RuntimeError as e:
            assert name in str(e), f"expected a {name} cross-coin message, got: {e}"
    # a legacy base58 address (ambiguous across the family) is also rejected
    try:
        orch.send("BLC", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "1.0")
        assert False, "legacy base58 destination was not rejected"
    except RuntimeError:
        pass
    # a correctly-prefixed BLC address PASSES the address guard (then fails later because
    # no daemon is running — but NOT with the address-format message)
    good = "blc1ql27pg0ttv2pvdcqe06dw220epn5yxj64p89800"
    try:
        orch.send("BLC", good, "1.0")
    except Exception as e:
        assert "valid BLC address" not in str(e) and " address — send " not in str(e), \
            f"correct BLC address was wrongly rejected by the guard: {e}"
    # malformed amounts (incl. inf/nan which Decimal accepts) get a FRIENDLY error
    for bad_amt in ("abc", "0", "", "1.2.3", "nan", "inf", "Infinity", "-inf"):
        try:
            orch.send("BLC", good, bad_amt)
            assert False, f"bad amount {bad_amt!r} not rejected"
        except RuntimeError as e:
            assert ("valid amount" in str(e) or "greater than zero" in str(e) or "invalid address or amount" in str(e)), \
                f"bad amount {bad_amt!r} gave unfriendly error: {str(e)[:80]}"
    # unknown coin -> friendly message (not a raw KeyError repr)
    try:
        orch.send("ZZZ", good, "1.0")
        assert False, "unknown coin not rejected"
    except RuntimeError as e:
        assert "unknown coin" in str(e), f"unknown coin gave: {str(e)[:80]}"


@case("send: a FAILED re-preview clears the pending tx (no stale broadcast on confirm)")
def _():
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    import sys
    from unified.orchestrator import Orchestrator
    orch = Orchestrator(python_bin=sys.executable, workspaces_root=wsroot,
                        datadirs_root=tempfile.mkdtemp(), servers={"BLC": "x:50002:s"})
    psbt_json = {"inputs": [{"value_sats": 150000}],
                 "outputs": [{"address": "blc1qdest", "value_sats": 120000},
                             {"address": "blc1qchange", "value_sats": 29000}]}
    orch.rpc = lambda t, cmd, *a, **k: psbt_json
    # preview #1 succeeds -> a tx is pending
    orch._run = lambda d, *a, **k: type("R", (), {"returncode": 0, "stdout": "PSBT_A", "stderr": ""})()
    orch.prepare_send("BLC", "blc1qdest", "0.0012")
    assert "BLC" in orch._pending, "preview should leave a pending tx"
    # preview #2 FAILS (e.g. transient not-connected) -> pending must be CLEARED, not stale
    orch._run = lambda d, *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "Not connected"})()
    try:
        orch.prepare_send("BLC", "blc1qotherdest", "0.9")
        assert False, "failed re-preview should raise"
    except RuntimeError:
        pass
    assert "BLC" not in orch._pending, "STALE pending after a failed re-preview"
    # confirm now has nothing to broadcast (cannot ship the stale A)
    try:
        orch.confirm_send("BLC")
        assert False, "confirm broadcast a stale tx"
    except RuntimeError as e:
        assert "no pending" in str(e), f"confirm gave: {str(e)[:80]}"


@case("send: fee preview computes fee = inputs - outputs and the destination amount")
def _():
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    import sys
    from unified.orchestrator import Orchestrator
    orch = Orchestrator(python_bin=sys.executable, workspaces_root=wsroot,
                        datadirs_root=tempfile.mkdtemp(), servers={"BLC": "x:50002:s"})
    # synthetic deserialized PSBT (inputs/outputs carry value_sats, like electrum's to_json)
    orch.rpc = lambda ticker, cmd, *a, **k: {
        "inputs": [{"value_sats": 100000}, {"value_sats": 50000}],
        "outputs": [
            {"address": "blc1qdest", "value_sats": 120000},     # to recipient
            {"address": "blc1qchangeaddr", "value_sats": 29000},  # change
        ],
    }
    fee, amount_sat = orch._psbt_fee_and_amount("BLC", "psbt-blob", "blc1qdest")
    assert fee == 1000, f"fee {fee} != 1000 (150000 in - 149000 out)"
    assert amount_sat == 120000, f"amount {amount_sat} != 120000 (the destination output)"
    # a negative fee (malformed/hostile deserialize) must raise, not silently send
    orch.rpc = lambda *a, **k: {"inputs": [{"value_sats": 1}], "outputs": [{"value_sats": 999, "address": "blc1qdest"}]}
    try:
        orch._psbt_fee_and_amount("BLC", "psbt", "blc1qdest")
        assert False, "negative fee not rejected"
    except RuntimeError:
        pass


# --------------------------------------------------------------------------- #
# Orchestrator resilience (no daemons; monkeypatched) — round-5 hardening
# --------------------------------------------------------------------------- #

def _orch():
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        return None
    import sys
    from unified.orchestrator import Orchestrator
    return Orchestrator(python_bin=sys.executable, workspaces_root=wsroot,
                        datadirs_root=tempfile.mkdtemp(), servers={})


@case("provision_all: one corrupt coin wallet does NOT block the other coins (per-coin isolation)")
def _():
    orch = _orch()
    if orch is None:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    bad = "PHO"
    orch.provision = lambda t, m, p="", **k: None
    orch.is_provisioned = lambda t: True

    def fake_load(t):
        if t == bad:
            raise RuntimeError(f"{t} load_wallet failed: corrupt wallet file")
        orch._loaded.add(t)
    orch.load = fake_load
    errors = orch.provision_all("abandon abandon abandon abandon abandon abandon "
                                "abandon abandon abandon abandon abandon about")
    assert bad in errors, "the corrupt coin should be reported in errors"
    healthy = [t for t in orch.daemons if t != bad]
    assert all(t in orch._loaded for t in healthy), \
        f"healthy coins were blocked by {bad}: loaded={orch._loaded}"


@case("merged_history: hostile/malformed per-coin history doesn't crash (non-dict + mixed timestamps)")
def _():
    orch = _orch()
    if orch is None:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    orch.history = lambda t: [
        {"txid": "a", "timestamp": 100},
        "not-a-dict",                       # hostile non-dict entry
        {"txid": "b", "timestamp": "bogus"},  # non-numeric timestamp
        {"txid": "c"},                      # missing timestamp
        12345,                              # hostile non-dict
    ]
    out = orch.merged_history(limit=10)
    # No crash; every surviving entry is a dict (non-dict/bogus entries filtered out);
    # capped at the limit. (Each coin contributes its 3 valid dicts; aggregated + capped.)
    assert isinstance(out, list) and out, "merged_history returned nothing / not a list"
    assert all(isinstance(e, dict) for e in out), "a non-dict entry survived"
    assert len(out) <= 10, f"limit not respected: {len(out)}"


@case("portfolio: a crashing/hostile price oracle never 500s the dashboard (amounts-only fallback)")
def _():
    orch = _orch()
    if orch is None:
        print("    (skipped: set ELECTRUM_WSROOT)", end="")
        return
    orch.rpc = lambda t, cmd, *a, **k: {"confirmed": "1.5"}

    class BoomOracle:
        def value_portfolio(self, balances):
            raise ValueError("hostile price blew up")
    orch.oracle = BoomOracle()
    p = orch.portfolio()
    assert isinstance(p, dict) and "coins" in p and "total" in p
    assert p["total"]["value_btc"] is None  # degraded to amounts-only, no crash


def main():
    import sys
    passed = failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}  -- {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {name}  -- {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} edge-case checks passed"
          + ("" if not failed else f"  ({failed} FAILED)"))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
