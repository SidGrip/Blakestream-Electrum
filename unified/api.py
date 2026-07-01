"""P5.1 — loopback HTTP/JSON API over the orchestrator.

This is the data layer the Electron renderer (or any local client) polls. It is
**loopback-only** (127.0.0.1) and stdlib-only (``http.server``). Read-mostly
endpoints:

  GET /health          -> {"ok": true, "coins": [...]}
  GET /coins           -> per-coin metadata {ticker, coin_type, hrp, rpc_port}
  GET /portfolio       -> orchestrator.portfolio() (Decimals serialized as strings)
  GET /getinfo/<COIN>  -> that daemon's getinfo
  GET /address/<COIN>  -> {"address": <first receive address>}

Security: bound to loopback for same-host IPC from the Electron app, gated by a
per-launch bearer token (constant-time compared) and a loopback-only Host check
(DNS-rebind defence). The orchestrator holds the keys/daemons.
"""

from __future__ import annotations

import errno
import hmac
import ipaddress
import json
import os
import shutil
import signal
import socket
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from unified import backup as wallet_backup, contacts, provisioning, vault
from unified.orchestrator import DexOrdersActiveError

DEFAULT_API_PORT = 57100


def _jsonable(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def make_handler(orchestrator, vault_path=None, token=None, shutdown_cb=None):
    # Serialise vault setup so two concurrent create/restore requests can't race past
    # the "vault exists?" check and mint a second seed over the one just backed up.
    setup_lock = threading.Lock()
    # Address-book sidecar lives next to the vault (owner-only dir family).
    contacts_path = (os.path.join(os.path.dirname(vault_path), "contacts.json")
                     if vault_path else None)
    # Brute-force friction for the secret-reveal routes (seed / private key): on top of the
    # vault's Argon2 + the 0.5s/wrong-password delay, cap the number of password attempts in a
    # rolling window. Loopback-only, so this is effectively a global cap. Shared across the
    # per-request Handler instances (ThreadingHTTPServer), guarded by a lock.
    reveal_lock = threading.Lock()
    reveal_attempts: list = []
    REVEAL_MAX, REVEAL_WINDOW = 10, 60

    def _pw_attempts_blocked() -> bool:
        """True if too many recent FAILED vault-password attempts are inside the rolling window.
        Read-only — does NOT itself count an attempt, so a CORRECT password never consumes budget
        (no self-inflicted lockout when a user reveals across several coins)."""
        with reveal_lock:
            now = time.monotonic()
            reveal_attempts[:] = [t for t in reveal_attempts if now - t < REVEAL_WINDOW]
            return len(reveal_attempts) >= REVEAL_MAX

    def _pw_record_failure() -> None:
        """Charge the brute-force budget — call ONLY on a wrong password."""
        with reveal_lock:
            reveal_attempts.append(time.monotonic())

    class Handler(BaseHTTPRequestHandler):
        # Per-connection socket timeout: a slowloris / stuck-body connection that sends
        # headers or the body slowly (or never finishes) must not park a worker thread
        # forever. Legit local requests complete in well under a second; 20s is generous.
        timeout = 20
        # The socket timeout above fires only on a STALLED single recv(); a slow drip (a byte
        # every few seconds) resets it and holds a thread-per-connection worker indefinitely.
        # A whole-connection wall-clock deadline closes the socket regardless, bounding such a
        # connection's lifetime. Keep it above the longest legitimate local RPC proxy call:
        # /dex/pay-lightning can wait up to 180s for Electrum's lnpay route attempt.
        CONN_DEADLINE = 240

        def setup(self):
            super().setup()
            self._deadline_timer = threading.Timer(self.CONN_DEADLINE, self._force_close)
            self._deadline_timer.daemon = True
            self._deadline_timer.start()

        def _force_close(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)   # unblock any pending recv -> worker exits
            except OSError:
                pass

        def finish(self):
            try:
                self._deadline_timer.cancel()
            except Exception:
                pass
            super().finish()

        def log_message(self, *args):
            pass  # quiet

        def _host_ok(self) -> bool:
            # Defence against DNS-rebinding: a browser that rebinds a hostname to
            # 127.0.0.1 still sends that hostname in Host. Require a loopback Host (the
            # IPC proxy and browser fetch always send 127.0.0.1:57100). A missing/empty
            # Host or a colon-prefixed value must NOT pass.
            host = self.headers.get("Host")
            if not host:
                return False
            host = host.strip()
            if host.startswith("["):           # [::1] or [::1]:port
                name = host[1:].split("]", 1)[0]
            elif host.count(":") == 1:          # host:port
                name = host.split(":", 1)[0]
            else:                               # bare host, or bare IPv6 like ::1
                name = host
            return name in ("127.0.0.1", "localhost", "::1")

        def _client_loopback_ok(self) -> bool:
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except Exception:
                return False

        def _check_auth(self) -> bool:
            # Opt-in per-launch bearer token. When unset (loopback dev / tests) auth is
            # a no-op; the packaged app sets ELECTRUM_API_TOKEN so another local user on
            # a multi-user host can't read balances/addresses or drive setup.
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            # constant-time compare so a local attacker can't time out the token.
            return hmac.compare_digest(auth[7:].strip(), token)

        def _send(self, code, payload):
            body = json.dumps(_jsonable(payload)).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _read_json_body(self, max_bytes=64 * 1024):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                self._send(400, {"error": "invalid content-length"})
                return None
            if length < 0 or length > max_bytes:
                self._send(413, {"error": "request body too large"})
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except Exception:
                self._send(400, {"error": "invalid json body"})
                return None
            if not isinstance(data, dict):
                self._send(400, {"error": "invalid json body"})
                return None
            return data

        def _coins_meta(self):
            meta = {}
            for ticker, d in orchestrator.daemons.items():
                c = orchestrator.coins.get(ticker, {})
                meta[ticker] = {
                    "ticker": ticker,
                    "coin_name": c.get("coin_name"),
                    "coin_type": c.get("coin_type"),
                    "hrp": c.get("segwit_hrp"),
                    "rpc_port": d.rpc_port,
                }
            return meta

        def do_GET(self):
            if not self._host_ok():
                self._send(403, {"error": "forbidden host"})
                return
            # Unauthenticated identity handshake: lets the client confirm it is talking to
            # ITS backend (spawned with the token) before sending any secret. A local
            # port-squatter that grabbed 127.0.0.1:57100 first holds no token and cannot
            # produce this proof, so the client refuses it. Carries no secret itself.
            path_only = self.path.split("?", 1)[0].strip("/")
            if path_only == "handshake":
                nonce = ""
                if "?" in self.path:
                    from urllib.parse import parse_qs
                    nonce = (parse_qs(self.path.split("?", 1)[1]).get("nonce") or [""])[0]
                proof = (hmac.new(token.encode(), nonce.encode(), "sha256").hexdigest()
                         if token else "")
                self._send(200, {"proof": proof})
                return
            # Local DEX discovery. It is default-off and Host/loopback gated. Unknown DEX
            # instances get a wallet-side approval prompt and no session token; only the
            # approved DEX receives import data for the local session.
            if path_only == "ready":
                if not self._client_loopback_ok():
                    self._send(403, {"error": "forbidden client"})
                    return
                from urllib.parse import parse_qs
                query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                dex_instance_id = (query.get("dex_instance_id") or [""])[0]
                dex_name = (query.get("dex_name") or [""])[0]
                status = orchestrator.dex_ready_status(dex_instance_id, dex_name)
                http_status = int(status.pop("_http_status", 200) or 200)
                if not status.get("integration_allowed"):
                    self._send(403, {"integration_allowed": False,
                                     "error": "local DEX integration disabled"})
                    return
                self._send(http_status, status)
                return
            if not self._check_auth():
                self._send(401, {"error": "unauthorized"})
                return
            parts = [p for p in self.path.split("?")[0].strip("/").split("/") if p]
            try:
                if not parts or parts == ["health"]:
                    self._send(200, {"ok": True, "coins": list(orchestrator.daemons)})
                elif parts == ["coins"]:
                    self._send(200, self._coins_meta())
                elif parts == ["portfolio"]:
                    self._send(200, orchestrator.portfolio())
                elif len(parts) == 2 and parts[0] == "getinfo":
                    self._send(200, orchestrator.getinfo(parts[1].upper()))
                elif len(parts) == 2 and parts[0] == "address":
                    self._send(200, {"address": orchestrator.first_address(parts[1].upper())})
                elif len(parts) == 2 and parts[0] == "receive":
                    coin = parts[1].upper()
                    self._send(200, {"address": orchestrator.receive_address(coin),
                                     "can_send": orchestrator.can_send(coin)})
                elif parts == ["history"]:
                    self._send(200, {"transactions": orchestrator.merged_history()})
                elif parts == ["lightning-history"]:
                    self._send(200, {"transactions": orchestrator.merged_ln_history()})
                elif len(parts) == 2 and parts[0] == "history":
                    self._send(200, {"transactions": orchestrator.history(parts[1].upper())})
                elif len(parts) == 2 and parts[0] == "addresses":
                    from urllib.parse import parse_qs
                    q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                    try:                                  # a non-numeric ?limit must be a clean 400, not a 500
                        limit = int((q.get("limit") or ["1000"])[0] or 1000)
                    except (TypeError, ValueError):
                        self._send(400, {"error": "limit must be a whole number"}); return
                    limit = max(0, min(limit, 5000))      # clamp negatives to 0, cap at 5000
                    kind = (q.get("filter") or ["receiving"])[0]
                    if kind not in ("receiving", "change", "all"):
                        kind = "receiving"
                    self._send(200, {"addresses": orchestrator.addresses(
                        parts[1].upper(), kind=kind, limit=limit)})
                elif parts == ["contacts"]:
                    self._send(200, {"contacts": contacts.list_contacts(contacts_path, key=orchestrator.contacts_key) if contacts_path else []})
                elif len(parts) == 2 and parts[0] == "contacts":
                    self._send(200, {"contacts": contacts.list_contacts(contacts_path, parts[1].upper(), key=orchestrator.contacts_key) if contacts_path else []})
                elif len(parts) == 3 and parts[0] == "lightning":
                    coin, sub = parts[1].upper(), parts[2]
                    if sub == "status":
                        self._send(200, orchestrator.ln_status(coin))
                    elif sub == "channels":
                        self._send(200, {"channels": orchestrator.ln_list_channels(coin)})
                    elif sub == "nodeid":
                        self._send(200, {"node_id": orchestrator.ln_nodeid(coin)})
                    elif sub == "history":
                        self._send(200, {"transactions": orchestrator.ln_history(coin)})
                    elif sub == "decode":
                        from urllib.parse import parse_qs
                        q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                        self._send(200, orchestrator.ln_decode(coin, (q.get("invoice") or [""])[0]))
                    elif sub == "peers":
                        self._send(200, {"peers": orchestrator.ln_list_peers(coin)})
                    elif sub == "gossip":
                        self._send(200, orchestrator.ln_gossip_info(coin))
                    elif sub == "requests":
                        self._send(200, {"requests": orchestrator.ln_requests(coin)})
                    else:
                        self._send(404, {"error": "not found", "path": self.path})
                elif len(parts) == 3 and parts[0] == "tools" and parts[2] in ("utxos", "master-pubkey", "wallet-info"):
                    coin = parts[1].upper()
                    if coin not in orchestrator.daemons:   # unknown coin / path-traversal -> clean 404
                        self._send(404, {"error": f"unknown coin {coin}"})
                    elif parts[2] == "utxos":
                        self._send(200, {"utxos": orchestrator.list_utxos(coin)})
                    elif parts[2] == "wallet-info":
                        self._send(200, orchestrator.wallet_info(coin))
                    else:
                        self._send(200, {"mpk": orchestrator.master_pubkey(coin)})
                elif parts == ["settings", "coin-colors"]:
                    self._send(200, orchestrator.coin_colors())
                elif parts == ["settings", "startup-coins"]:
                    self._send(200, orchestrator.autostart_settings())
                elif len(parts) == 2 and parts[0] == "settings":
                    self._send(200, orchestrator.network_settings(parts[1].upper()))
                elif parts == ["price-sources"]:
                    self._send(200, orchestrator.price_sources_public())
                elif parts == ["fx", "currencies"]:
                    self._send(200, orchestrator.fx_currencies())
                elif parts == ["startup"]:
                    self._send(200, orchestrator.startup_status())
                elif parts == ["setup", "status"]:
                    self._send(200, {"provisioned": orchestrator.all_provisioned(),
                                     "vault_exists": bool(vault_path and vault.vault_exists(vault_path))})
                elif parts == ["session", "status"]:
                    self._send(200, {"locked": orchestrator.locked(),
                                     "vault_exists": bool(vault_path and vault.vault_exists(vault_path))})
                elif parts == ["setup", "progress"]:
                    self._send(200, orchestrator.get_progress())
                elif parts == ["dex", "integration"]:
                    self._send(200, orchestrator.dex_integration_settings())
                else:
                    self._send(404, {"error": "not found", "path": self.path})
            except KeyError as e:
                self._send(404, {"error": f"unknown coin {e}"})
            except Exception as e:  # surface daemon/RPC errors as 500
                self._send(500, {"error": str(e)[:300]})

        def do_POST(self):
            if not self._host_ok():
                self._send(403, {"error": "forbidden host"})
                return
            parts = [p for p in self.path.split("?")[0].strip("/").split("/") if p]
            # DEX presence heartbeat. It is not a signing route, but it must still be
            # bound to the approved DEX identity/session so a second local process cannot
            # make the wallet UI believe it is connected.
            if parts == ["dex", "heartbeat"]:
                if not self._client_loopback_ok():
                    self._send(403, {"error": "forbidden client"})
                    return
                data = self._read_json_body(max_bytes=4 * 1024)
                if data is None:
                    return
                allowed_keys = {"dex_instance_id", "dex_session_token"}
                extra_keys = sorted(set(data) - allowed_keys)
                if extra_keys:
                    self._send(400, {"error": f"unexpected field(s): {', '.join(extra_keys)}"})
                    return
                missing_keys = [k for k in ("dex_instance_id", "dex_session_token") if data.get(k) in (None, "")]
                if missing_keys:
                    self._send(400, {"error": f"missing field(s): {', '.join(missing_keys)}"})
                    return
                try:
                    status = orchestrator.record_dex_heartbeat(
                        data.get("dex_instance_id"),
                        data.get("dex_session_token"),
                    )
                except PermissionError as e:
                    self._send(403, {"error": str(e)[:200]})
                    return
                if not status.get("allow_local_dex"):
                    self._send(403, {**status, "error": "local DEX integration disabled"})
                    return
                self._send(200, status)
                return
            # Pairing cancel is token-free because the DEX has not been approved yet.
            # It may only clear the pending request for the exact DEX identity supplied
            # by that same loopback DEX instance; it cannot clear another pending DEX.
            if parts == ["dex", "cancel-pairing"]:
                if not self._client_loopback_ok():
                    self._send(403, {"error": "forbidden client"})
                    return
                data = self._read_json_body(max_bytes=4 * 1024)
                if data is None:
                    return
                allowed_keys = {"dex_instance_id"}
                extra_keys = sorted(set(data) - allowed_keys)
                if extra_keys:
                    self._send(400, {"error": f"unexpected field(s): {', '.join(extra_keys)}"})
                    return
                if data.get("dex_instance_id") in (None, ""):
                    self._send(400, {"error": "missing field(s): dex_instance_id"})
                    return
                status = orchestrator.cancel_pending_dex_pairing(data.get("dex_instance_id"))
                self._send(200, status)
                return
            # Token-free local DEX funding route. It is intentionally outside the
            # Electron bearer token so a separately-launched DEX can use it, but
            # it is still loopback/Host gated and requires the in-memory /ready
            # DEX session token plus the owner-only per-coin RPC password.
            if parts == ["dex", "fund-htlc"]:
                if not self._client_loopback_ok():
                    self._send(403, {"error": "forbidden client"})
                    return
                data = self._read_json_body(max_bytes=16 * 1024)
                if data is None:
                    return
                allowed_keys = {"coin", "address", "amount", "dex_instance_id", "dex_session_token", "rpc_password",
                                "swap_id", "order_id", "description"}
                extra_keys = sorted(set(data) - allowed_keys)
                if extra_keys:
                    self._send(400, {"error": f"unexpected field(s): {', '.join(extra_keys)}"})
                    return
                missing_keys = [k for k in ("coin", "address", "amount", "dex_instance_id", "dex_session_token", "rpc_password")
                                if data.get(k) in (None, "")]
                if missing_keys:
                    self._send(400, {"error": f"missing field(s): {', '.join(missing_keys)}"})
                    return
                try:
                    result = orchestrator.dex_fund_htlc(
                        data.get("coin"),
                        data.get("address"),
                        data.get("amount"),
                        data.get("dex_session_token"),
                        data.get("rpc_password"),
                        data.get("dex_instance_id"),
                        swap_id=data.get("swap_id"),
                        order_id=data.get("order_id"),
                        description=data.get("description"),
                    )
                except PermissionError as e:
                    self._send(403, {"error": str(e)[:200]})
                    return
                except KeyError as e:
                    self._send(404, {"error": f"unknown coin {e}"})
                    return
                except ValueError as e:
                    self._send(400, {"error": str(e)[:200]})
                    return
                except Exception as e:
                    self._send(500, {"error": str(e)[:300]})
                    return
                self._send(200, result)
                return
            if parts == ["dex", "pay-lightning"]:
                if not self._client_loopback_ok():
                    self._send(403, {"error": "forbidden client"})
                    return
                data = self._read_json_body(max_bytes=64 * 1024)
                if data is None:
                    return
                allowed_keys = {"coin", "invoice", "dex_instance_id", "dex_session_token", "rpc_password",
                                "timeout", "max_cltv", "max_fee_msat", "swap_id", "order_id",
                                "description"}
                extra_keys = sorted(set(data) - allowed_keys)
                if extra_keys:
                    self._send(400, {"error": f"unexpected field(s): {', '.join(extra_keys)}"})
                    return
                missing_keys = [k for k in ("coin", "invoice", "dex_instance_id", "dex_session_token", "rpc_password")
                                if data.get(k) in (None, "")]
                if missing_keys:
                    self._send(400, {"error": f"missing field(s): {', '.join(missing_keys)}"})
                    return
                try:
                    result = orchestrator.dex_pay_lightning(
                        data.get("coin"),
                        data.get("invoice"),
                        data.get("dex_session_token"),
                        data.get("rpc_password"),
                        data.get("dex_instance_id"),
                        timeout=data.get("timeout"),
                        max_cltv=data.get("max_cltv"),
                        max_fee_msat=data.get("max_fee_msat"),
                        swap_id=data.get("swap_id"),
                        order_id=data.get("order_id"),
                        description=data.get("description"),
                    )
                except PermissionError as e:
                    self._send(403, {"error": str(e)[:200]})
                    return
                except KeyError as e:
                    self._send(404, {"error": f"unknown coin {e}"})
                    return
                except ValueError as e:
                    self._send(400, {"error": str(e)[:200]})
                    return
                except Exception as e:
                    self._send(500, {"error": str(e)[:300]})
                    return
                self._send(200, result)
                return
            if not self._check_auth():
                self._send(401, {"error": "unauthorized"})
                return
            data = self._read_json_body()
            if data is None:
                return
            try:
                if parts == ["shutdown"]:
                    # Graceful, cross-platform stop: the Electron app POSTs this on quit so
                    # the supervisor stops all six daemons BEFORE exiting. On Windows a
                    # SIGTERM would hard-kill the supervisor and orphan the daemons.
                    try:
                        orchestrator.stop_all()
                    finally:
                        self._send(200, {"ok": True})
                        if shutdown_cb:
                            shutdown_cb()
                    return
                if parts in (["setup", "create"], ["setup", "restore"]):
                    # Lock so the exists-check and the create are atomic — a double-submit
                    # must not mint a second seed over the phrase the user just backed up.
                    with setup_lock:
                        if vault_path and vault.vault_exists(vault_path):
                            self._send(409, {"error": "vault already exists; unlock it, or "
                                                      "remove the vault file AND the per-coin "
                                                      "wallet datadirs to start fresh"})
                            return
                        pw = data.get("password")
                        if not pw or len(str(pw)) < 8:
                            self._send(400, {"error": "password must be at least 8 characters"})
                            return
                        if parts == ["setup", "create"]:
                            mnemonic = provisioning.generate_mnemonic()
                        else:  # restore
                            mnemonic = (data.get("mnemonic") or "").strip()
                            if not mnemonic:
                                self._send(400, {"error": "password and mnemonic required"})
                                return
                            if not provisioning.is_valid_bip39(mnemonic):
                                self._send(400, {"error": "invalid BIP39 mnemonic"})
                                return
                        if vault_path:
                            vault.create_vault(vault_path, mnemonic, pw)
                        errors = orchestrator.provision_all(mnemonic)
                        if errors:
                            # A stale wallet from a different seed (or a daemon failure)
                            # blocked the new seed. Don't present it as safely backed up —
                            # roll the vault back so the user can clear datadirs and retry.
                            if vault_path:
                                try:
                                    os.remove(vault_path)
                                except OSError:
                                    pass
                            self._send(409, {"error": "setup blocked by existing wallets from a "
                                                      "different seed; clear the per-coin datadirs "
                                                      "and retry", "details": errors})
                            return
                        if parts == ["setup", "create"]:
                            self._send(200, {"ok": True, "mnemonic": mnemonic})  # show ONCE for backup
                        else:
                            self._send(200, {"ok": True})
                    return
                if parts == ["setup", "unlock"]:
                    pw = data.get("password")
                    if not pw:
                        self._send(400, {"error": "password required"})
                        return
                    if not (vault_path and vault.vault_exists(vault_path)):
                        self._send(400, {"error": "no vault to unlock"})
                        return
                    # Unlock verifies the same vault password as the reveal routes, so it shares the
                    # brute-force cap — otherwise an attacker could grind the password here to bypass
                    # the reveal-route limit (same Argon2 oracle).
                    if _pw_attempts_blocked():
                        self._send(429, {"error": "too many attempts — wait a minute and try again"}); return
                    # Serialize unlock under the same lock: only one Argon2 derivation at
                    # a time (no parallel memory amplification) and one provision_all at a
                    # time (no re-entrant daemon-subprocess fan-out). A small delay on a
                    # bad password adds brute-force friction on top of Argon2's cost.
                    with setup_lock:
                        try:
                            mnemonic = vault.unlock_vault(vault_path, pw)
                        except vault.BadPassword:
                            _pw_record_failure()
                            time.sleep(0.5)
                            self._send(401, {"error": "wrong password"})
                            return
                        except ValueError as e:          # e.g. unsupported vault version -> 400, not 500
                            self._send(400, {"error": str(e)[:120]})
                            return
                        errors = orchestrator.provision_all(mnemonic)
                    self._send(200, {"ok": True, "errors": errors} if errors else {"ok": True})
                elif parts == ["session", "lock"]:
                    # Soft lock: drop the in-memory seed-derived keys. Daemons stay up + synced; signing /
                    # reveal / change-password need the password re-entered. Idempotent, no password.
                    orchestrator.clear_session_keys()
                    self._send(200, {"ok": True, "locked": True})
                elif parts == ["session", "unlock"]:
                    pw = data.get("password")
                    if not pw:
                        self._send(400, {"error": "password required"}); return
                    if not (vault_path and vault.vault_exists(vault_path)):
                        self._send(400, {"error": "no vault to unlock"}); return
                    if _pw_attempts_blocked():
                        self._send(429, {"error": "too many attempts — wait a minute and try again"}); return
                    with setup_lock:
                        try:
                            mnemonic = vault.unlock_vault(vault_path, pw)
                        except vault.BadPassword:
                            _pw_record_failure(); time.sleep(0.5)
                            self._send(401, {"error": "wrong password"}); return
                        except ValueError as e:
                            self._send(400, {"error": str(e)[:120]}); return
                        # Re-install the seed-derived keys WITHOUT re-provisioning — the daemons are already
                        # up and the wallets stay loaded; locking only dropped the in-memory passwords.
                        wallet_pws, contacts_key = vault.derive_session_keys(mnemonic, list(orchestrator.daemons))
                        orchestrator.set_session_keys(wallet_pws, contacts_key)
                        del mnemonic
                    self._send(200, {"ok": True, "locked": False})
                elif parts == ["backup", "export"]:
                    pw = data.get("password")
                    path = str(data.get("path") or "").strip()
                    if not pw or not path:
                        self._send(400, {"error": "password and backup path required"}); return
                    if not (vault_path and vault.vault_exists(vault_path)):
                        self._send(400, {"error": "no wallet vault to back up"}); return
                    if _pw_attempts_blocked():
                        self._send(429, {"error": "too many attempts — wait a minute and try again"}); return
                    with setup_lock:
                        try:
                            mnemonic = vault.unlock_vault(vault_path, pw)
                        except vault.BadPassword:
                            _pw_record_failure(); time.sleep(0.5)
                            self._send(401, {"error": "wrong password"}); return
                        except ValueError as e:
                            self._send(400, {"error": str(e)[:120]}); return
                        del mnemonic
                        try:
                            result = wallet_backup.create_backup(orchestrator.datadirs_root, path, str(pw))
                        except wallet_backup.BackupError as e:
                            self._send(400, {"error": str(e)[:160]}); return
                        except Exception as e:
                            self._send(500, {"error": f"backup failed: {str(e)[:120]}"}); return
                    self._send(200, result)
                elif parts == ["backup", "restore"]:
                    pw = data.get("password")
                    path = str(data.get("path") or "").strip()
                    if not pw or not path:
                        self._send(400, {"error": "password and backup path required"}); return
                    if vault_path and vault.vault_exists(vault_path):
                        self._send(409, {"error": "wallet already exists; unlock it or back up and remove existing wallet data before restoring"})
                        return
                    with setup_lock:
                        try:
                            result = wallet_backup.restore_backup(path, orchestrator.datadirs_root, str(pw))
                        except wallet_backup.BackupError as e:
                            self._send(400, {"error": str(e)[:160]}); return
                        except Exception as e:
                            self._send(500, {"error": f"restore failed: {str(e)[:120]}"}); return
                        # Restored configs/wallet files should be loaded by a fresh backend process.
                        try:
                            orchestrator.stop_all()
                        except Exception:
                            pass
                    result["needs_restart"] = True
                    self._send(200, result)
                elif parts == ["dex", "integration"]:
                    status = orchestrator.dex_integration_settings()
                    if "allow_local_dex" in data:
                        status = orchestrator.set_dex_integration(data.get("allow_local_dex"))
                    if "start_local_dex_on_startup" in data:
                        status = orchestrator.set_dex_start_on_startup(data.get("start_local_dex_on_startup"))
                    if "approve_dex_id" in data:
                        try:
                            status = orchestrator.approve_dex_pairing(
                                data.get("approve_dex_id"),
                                data.get("approve_dex_name"),
                            )
                        except ValueError as e:
                            self._send(400, {"error": str(e)[:120]}); return
                    if data.get("clear_pending_dex_pair"):
                        status = orchestrator.clear_pending_dex_pairing()
                    if data.get("forget_paired_dex"):
                        status = orchestrator.forget_dex_pairing()
                    self._send(200, status)
                elif parts == ["settings", "coin-colors"]:
                    colors = data.get("colors")
                    if not isinstance(colors, dict):
                        self._send(400, {"error": "colors object required"})
                        return
                    self._send(200, orchestrator.set_coin_colors(colors))
                elif parts == ["settings", "startup-coins"]:
                    if "include_all" not in data and "coins" not in data:
                        self._send(400, {"error": "include_all and/or coins required"}); return
                    self._send(200, orchestrator.set_autostart(
                        data.get("include_all", True), data.get("coins") or []))
                elif len(parts) == 3 and parts[0] == "coins" and parts[2] in ("start", "stop"):
                    coin = parts[1].upper()
                    if coin not in orchestrator.daemons:
                        self._send(404, {"error": f"unknown coin {coin}"}); return
                    try:
                        if parts[2] == "start":
                            self._send(200, orchestrator.start_coin(coin))
                        else:
                            self._send(200, orchestrator.stop_coin(coin, force=bool(data.get("force"))))
                    except DexOrdersActiveError:
                        self._send(409, {"error": "dex_orders_active",
                                         "message": f"{coin} is connected to the DEX — confirm to stop it."})
                    except Exception as e:
                        self._send(500, {"error": str(e)[:300]})
                elif parts == ["wallet", "change-password"]:
                    cur = data.get("current_password")
                    new = data.get("new_password")
                    if not cur or not new:
                        self._send(400, {"error": "current and new password required"}); return
                    if len(str(new)) < 8:
                        self._send(400, {"error": "new password must be at least 8 characters"}); return
                    if str(new) == str(cur):
                        self._send(400, {"error": "new password must differ from the current one"}); return
                    if not (vault_path and vault.vault_exists(vault_path)):
                        self._send(400, {"error": "no vault"}); return
                    if _pw_attempts_blocked():
                        self._send(429, {"error": "too many attempts — wait a minute and try again"}); return
                    # Re-encrypt the vault (the seed) under the new password. The seed and the per-coin
                    # seed-derived daemon passwords are unchanged, so nothing else needs re-encrypting.
                    # Safety: back up the encrypted blob, re-encrypt, then VERIFY the new password decrypts
                    # to the SAME seed; on ANY failure restore the backup so the old password always works.
                    with setup_lock:
                        try:
                            before = vault.unlock_vault(vault_path, cur)
                        except vault.BadPassword:
                            _pw_record_failure(); time.sleep(0.5)
                            self._send(401, {"error": "wrong current password"}); return
                        except ValueError as e:
                            self._send(400, {"error": str(e)[:120]}); return
                        bak = vault_path + ".bak"
                        try:
                            shutil.copyfile(vault_path, bak)
                        except OSError as e:
                            self._send(500, {"error": f"could not back up vault: {e}"[:120]}); return
                        try:
                            vault.change_password(vault_path, cur, new)
                            if vault.unlock_vault(vault_path, new) != before:
                                raise RuntimeError("verification mismatch")
                        except Exception as e:
                            try:
                                os.replace(bak, vault_path)   # restore — the current password still works
                            except OSError:
                                pass
                            self._send(500, {"error": f"password change aborted, your current password still works ({str(e)[:80]})"}); return
                        finally:
                            del before
                        try:
                            os.remove(bak)
                        except OSError:
                            pass
                    self._send(200, {"ok": True})
                elif len(parts) == 3 and parts[0] == "send" and parts[2] == "preview":
                    # Build (do not broadcast) and return the fee so the Review screen can
                    # show amount + fee + total before the user confirms.
                    coin = parts[1].upper()
                    address = str(data.get("address") or "").strip()
                    amount = str(data.get("amount") or "").strip()
                    fee_rate = data.get("fee_rate") or None   # optional sat/byte; None => auto
                    from_coins = data.get("from_coins") or None  # optional coin-control input list
                    if not address or not amount:
                        self._send(400, {"error": "address and amount are required"})
                        return
                    try:
                        self._send(200, orchestrator.prepare_send(
                            coin, address, amount, feerate=fee_rate, from_coins=from_coins))
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif len(parts) == 3 and parts[0] == "send" and parts[2] == "confirm":
                    # Sign + broadcast the previewed tx (same tx the preview priced).
                    coin = parts[1].upper()
                    try:
                        self._send(200, {"txid": orchestrator.confirm_send(coin)})
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif len(parts) == 2 and parts[0] == "send":
                    coin = parts[1].upper()
                    address = str(data.get("address") or "").strip()
                    amount = str(data.get("amount") or "").strip()
                    if not address or not amount:
                        self._send(400, {"error": "address and amount are required"})
                        return
                    try:
                        txid = orchestrator.send(coin, address, amount)
                    except Exception as e:
                        # invalid address / insufficient funds / not connected -> 400 with message
                        self._send(400, {"error": str(e)[:300]})
                        return
                    self._send(200, {"txid": txid})
                elif len(parts) == 3 and parts[0] == "receive" and parts[2] == "new":
                    # Mint a fresh receiving address (advances past the current unused one)
                    # for users who want a distinct address per payment.
                    self._send(200, {"address": orchestrator.new_receive_address(parts[1].upper())})
                elif parts == ["contacts"]:
                    if not contacts_path:
                        self._send(503, {"error": "contacts unavailable (no vault dir)"})
                        return
                    coin = str(data.get("coin") or "").upper().strip()
                    address = str(data.get("address") or "").strip()
                    label = str(data.get("label") or "").strip()
                    if not coin or not address:
                        self._send(400, {"error": "coin and address are required"})
                        return
                    if not orchestrator.validate_address(coin, address):
                        self._send(400, {"error": f"not a valid {coin} address"})
                        return
                    self._send(200, {"contact": contacts.add(contacts_path, coin, address, label, key=orchestrator.contacts_key)})
                elif parts == ["contacts", "delete"]:
                    if not contacts_path:
                        self._send(503, {"error": "contacts unavailable (no vault dir)"})
                        return
                    self._send(200, {"ok": contacts.delete(contacts_path, str(data.get("id") or "").strip(), key=orchestrator.contacts_key)})
                elif len(parts) >= 3 and parts[0] == "lightning":
                    coin = parts[1].upper()
                    sub = "/".join(parts[2:])
                    try:
                        if sub == "enable":
                            self._send(200, orchestrator.enable_lightning(coin))
                        elif sub == "channels/open":
                            cs = str(data.get("connection_string") or "").strip()
                            amount = str(data.get("amount") or "").strip()
                            push_amount = str(data.get("push_amount") or "").strip()
                            if not cs or not amount:
                                self._send(400, {"error": "connection_string and amount are required"})
                                return
                            self._send(200, orchestrator.ln_open(coin, cs, amount, push_amount))
                        elif sub == "channels/close":
                            cp = str(data.get("channel_point") or "").strip()
                            if not cp:
                                self._send(400, {"error": "channel_point is required"})
                                return
                            self._send(200, orchestrator.ln_close(coin, cp, bool(data.get("force"))))
                        elif sub == "pay":
                            inv = str(data.get("invoice") or "").strip()
                            if not inv:
                                self._send(400, {"error": "invoice is required"})
                                return
                            self._send(200, orchestrator.ln_pay(coin, inv))
                        elif sub == "invoice":
                            amount = str(data.get("amount") or "").strip()
                            self._send(200, orchestrator.ln_invoice(
                                coin, amount, str(data.get("memo") or ""), str(data.get("expiry") or "3600")))
                        elif sub == "channels/export-backup":
                            cp = str(data.get("channel_point") or "").strip()
                            if not cp:
                                self._send(400, {"error": "channel_point is required"}); return
                            self._send(200, {"backup": orchestrator.ln_export_backup(coin, cp)})
                        elif sub == "channels/import-backup":
                            bk = str(data.get("backup") or "").strip()
                            if not bk:
                                self._send(400, {"error": "backup is required"}); return
                            self._send(200, {"result": orchestrator.ln_import_backup(coin, bk)})
                        elif sub == "channels/request-close":
                            cp = str(data.get("channel_point") or "").strip()
                            if not cp:
                                self._send(400, {"error": "channel_point is required"}); return
                            self._send(200, orchestrator.ln_request_force_close(
                                coin, cp, str(data.get("connection_string") or "")))
                        elif sub == "peers/add":
                            cs = str(data.get("connection_string") or "").strip()
                            if not cs:
                                self._send(400, {"error": "connection_string is required"}); return
                            self._send(200, orchestrator.ln_add_peer(coin, cs))
                        elif sub == "requests/delete":
                            rid = str(data.get("request_id") or "").strip()
                            if not rid:
                                self._send(400, {"error": "request_id is required"}); return
                            self._send(200, {"result": orchestrator.ln_delete_request(coin, rid)})
                        else:
                            self._send(404, {"error": "not found", "path": self.path})
                    except Exception as e:
                        # daemon raises "Lightning not enabled in this wallet" until enabled
                        msg = str(e)[:300]
                        self._send(409 if "ightning" in msg else 400, {"error": msg})
                elif len(parts) >= 3 and parts[0] == "tools":
                    # Electrum "Tools" menu features, grouped per coin under /tools/<COIN>/….
                    # Read-only / offline tools first; later groups append more sub-routes here.
                    coin = parts[1].upper()
                    if coin not in orchestrator.daemons:   # unknown coin / path-traversal segment -> clean 404
                        self._send(404, {"error": f"unknown coin {coin}"}); return
                    sub = "/".join(parts[2:])
                    try:
                        if sub == "load-tx":
                            tx = str(data.get("tx") or "").strip()
                            if not tx:
                                self._send(400, {"error": "tx is required"}); return
                            self._send(200, orchestrator.load_transaction(coin, tx))
                        elif sub == "fetch-tx":
                            txid = str(data.get("txid") or "").strip()
                            if not txid:
                                self._send(400, {"error": "txid is required"}); return
                            self._send(200, orchestrator.fetch_transaction(coin, txid))
                        elif sub == "broadcast":
                            tx = str(data.get("tx") or "").strip()
                            if not tx:
                                self._send(400, {"error": "tx is required"}); return
                            self._send(200, {"txid": orchestrator.broadcast_transaction(coin, tx)})
                        elif sub == "sign-message":
                            address = str(data.get("address") or "").strip()
                            message = str(data.get("message") or "")
                            if not address:
                                self._send(400, {"error": "address is required"}); return
                            self._send(200, {"signature": orchestrator.sign_message(coin, address, message)})
                        elif sub == "verify-message":
                            address = str(data.get("address") or "").strip()
                            signature = str(data.get("signature") or "").strip()
                            message = str(data.get("message") or "")
                            if not address or not signature:
                                self._send(400, {"error": "address and signature are required"}); return
                            self._send(200, {"valid": orchestrator.verify_message(coin, address, signature, message)})
                        elif sub == "freeze-address":
                            address = str(data.get("address") or "").strip()
                            if not address:
                                self._send(400, {"error": "address is required"}); return
                            frozen = bool(data.get("frozen"))
                            self._send(200, {"ok": orchestrator.set_address_frozen(coin, address, frozen)})
                        elif sub == "encrypt-message":
                            key = str(data.get("key") or "").strip()
                            message = str(data.get("message") or "")
                            if not key:
                                self._send(400, {"error": "public key or address is required"}); return
                            self._send(200, {"encrypted": orchestrator.encrypt_message(coin, key, message)})
                        elif sub == "decrypt-message":
                            key = str(data.get("key") or "").strip()
                            encrypted = str(data.get("encrypted") or "").strip()
                            if not key or not encrypted:
                                self._send(400, {"error": "public key/address and encrypted message are required"}); return
                            self._send(200, {"message": orchestrator.decrypt_message(coin, key, encrypted)})
                        elif sub == "pay-to-many":
                            outputs = data.get("outputs")
                            if not isinstance(outputs, list) or not outputs:
                                self._send(400, {"error": "outputs are required"}); return
                            feerate = data.get("feerate") or None
                            from_coins = data.get("from_coins") or None
                            self._send(200, orchestrator.pay_to_many(coin, outputs, feerate, from_coins))
                        elif sub == "sweep":
                            privkey = str(data.get("privkey") or "").strip()
                            destination = str(data.get("destination") or "").strip()
                            if not privkey or not destination:
                                self._send(400, {"error": "private key and destination are required"}); return
                            feerate = data.get("feerate") or None
                            self._send(200, orchestrator.sweep_to(coin, privkey, destination, feerate))
                        elif sub == "bump-fee":
                            tx = str(data.get("tx") or "").strip()
                            new_feerate = data.get("new_feerate") or data.get("feerate")
                            if not tx or not new_feerate:
                                self._send(400, {"error": "transaction and new fee rate are required"}); return
                            self._send(200, orchestrator.bump_fee(coin, tx, str(new_feerate)))
                        elif sub in ("reveal-seed", "export-privkey"):
                            # SENSITIVE: reveals the master seed / an address private key. Re-verify
                            # the wallet password against the VAULT here (don't trust the in-memory
                            # session key for an unlocked-but-unattended app), under setup_lock so the
                            # Argon2 derivation can't run in parallel; a small delay on a bad password
                            # adds brute-force friction.
                            password = data.get("password")
                            if not password:
                                self._send(400, {"error": "password is required"}); return
                            if not (vault_path and vault.vault_exists(vault_path)):
                                self._send(400, {"error": "no vault to verify against"}); return
                            if _pw_attempts_blocked():
                                self._send(429, {"error": "too many attempts — wait a minute and try again"}); return
                            with setup_lock:
                                try:
                                    mnemonic = vault.unlock_vault(vault_path, password)
                                except vault.BadPassword:
                                    _pw_record_failure()
                                    time.sleep(0.5)
                                    self._send(401, {"error": "wrong password"}); return
                                except ValueError as e:   # unsupported vault version -> 400, not 500
                                    self._send(400, {"error": str(e)[:120]}); return
                            if sub == "reveal-seed":
                                # The master recovery phrase is global (one seed -> all coins), held
                                # in the vault — not in any per-coin wallet. Return it directly.
                                self._send(200, {"seed": mnemonic})
                            else:
                                address = str(data.get("address") or "").strip()
                                if not address:
                                    self._send(400, {"error": "address is required"}); return
                                self._send(200, {"privkey": orchestrator.export_privkey(coin, address)})
                        else:
                            self._send(404, {"error": "not found", "path": self.path})
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif len(parts) == 2 and parts[0] == "label":
                    coin = parts[1].upper()
                    key = str(data.get("key") or "").strip()
                    label = str(data.get("label") or "")
                    if not key:
                        self._send(400, {"error": "key is required"}); return
                    try:
                        orchestrator.set_label(coin, key, label)
                        self._send(200, {"ok": True})
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif len(parts) == 3 and parts[0] == "settings" and parts[2] == "proxy":
                    coin = parts[1].upper()
                    if coin not in orchestrator.daemons:
                        self._send(404, {"error": f"unknown coin {coin}"}); return
                    # Restarts the daemon — serialize against a concurrent set_server/bring-up.
                    try:
                        with setup_lock:
                            result = orchestrator.set_proxy(
                                coin, enable=bool(data.get("enable")),
                                host=data.get("host", ""), port=data.get("port", 0),
                                user=data.get("user", ""), password=data.get("password", ""))
                        self._send(200, result)
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif len(parts) == 2 and parts[0] == "settings":
                    coin = parts[1].upper()
                    # Fee policy update (fee_mode / fee_sat_per_byte) vs a server update.
                    if "fee_mode" in data or "fee_sat_per_byte" in data:
                        try:
                            self._send(200, orchestrator.set_fee_policy(
                                coin, data.get("fee_mode"), data.get("fee_sat_per_byte")))
                        except Exception as e:
                            self._send(400, {"error": str(e)[:300]})
                        return
                    server = str(data.get("server") or "").strip()
                    if not server:
                        self._send(400, {"error": "server is required"})
                        return
                    try:
                        self._send(200, orchestrator.set_server(coin, server))
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                elif parts == ["price-sources"]:
                    op = str(data.get("op") or "").strip()
                    try:
                        if op == "set_enabled":
                            self._send(200, orchestrator.set_price_enabled(data.get("enabled")))
                        elif op == "set_poll":
                            self._send(200, orchestrator.set_poll_seconds(data.get("seconds")))
                        elif op == "set_display":
                            self._send(200, orchestrator.set_display_prefs(
                                data.get("fiatCurrency"), data.get("displayFiat")))
                        elif op == "add":
                            self._send(200, orchestrator.add_price_source(data.get("source") or {}))
                        elif op == "update":
                            self._send(200, orchestrator.update_price_source(
                                data.get("id"), data.get("source") or {}))
                        elif op == "remove":
                            self._send(200, orchestrator.remove_price_source(data.get("id")))
                        elif op == "set_source_enabled":
                            self._send(200, orchestrator.set_source_enabled(
                                data.get("id"), data.get("enabled")))
                        elif op == "reorder":
                            self._send(200, orchestrator.reorder_price_sources(data.get("order")))
                        elif op == "test":
                            self._send(200, orchestrator.test_price_source(
                                data.get("source") or {}, data.get("ticker"), data.get("fiat")))
                        else:
                            self._send(400, {"error": "unknown op"})
                    except Exception as e:
                        self._send(400, {"error": str(e)[:300]})
                else:
                    self._send(404, {"error": "not found", "path": self.path})
            except Exception as e:
                self._send(500, {"error": str(e)[:300]})

    return Handler


def make_server(orchestrator, host: str = "127.0.0.1", port: int = DEFAULT_API_PORT,
                vault_path=None, token=None, shutdown_cb=None):
    return ThreadingHTTPServer(
        (host, port), make_handler(orchestrator, vault_path, token, shutdown_cb))


def _pids_listening_on(port: int) -> set:
    """PIDs owning a TCP LISTEN socket on <port> (Linux /proc, no deps)."""
    want = f"{port:04X}"
    inodes = set()
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        for line in rows:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":   # 0A = TCP_LISTEN
                continue
            local = parts[1].rsplit(":", 1)            # IPADDR:PORT (hex)
            if len(local) == 2 and local[1].upper() == want:
                inodes.add(parts[9])
    if not inodes:
        return set()
    pids = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            fds = os.listdir(f"/proc/{entry}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                tgt = os.readlink(f"/proc/{entry}/fd/{fd}")
            except OSError:
                continue
            if tgt.startswith("socket:[") and tgt[8:-1] in inodes:
                pids.add(int(entry))
                break
    return pids


def _reap_foreign_supervisor(port: int) -> None:
    """Free our fixed API ``port`` by killing whatever leftover ``electrum-backend`` is squatting it
    — e.g. a hard-killed previous run, or a stale PyInstaller CHILD whose bootloader parent was
    SIGTERM'd (which is exactly what an Electron auto-restart collides with). We run this ONLY after
    our own bind fails (EADDRINUSE), so we never hold the port here — the owner is therefore the
    squatter, not us. We target the actual port owner (so a same-mount/same-pgroup leftover from a
    restart loop is reaped, unlike a name+path scan), self-protect by PID/PPID, and confirm the owner
    is an electrum-backend before killing so an unrelated process is never touched. Linux /proc only."""
    if not os.path.isdir("/proc"):       # non-Linux: skip (cross-platform reaping is a future item)
        return
    me, parent = os.getpid(), os.getppid()
    for pid in _pids_listening_on(port):
        if pid in (me, parent):
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read()
        except OSError:                  # process vanished or unreadable
            continue
        argv0 = cmd.split(b"\x00")[0].decode("utf-8", "replace")
        if os.path.basename(argv0) != "electrum-backend":
            continue                     # only ever reap our own binary; never an unrelated squatter
        if b"--serve" not in cmd or b"multi" not in cmd:
            continue                     # and only a multi-serve supervisor, never a CLI subcommand
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        for _ in range(15):              # up to ~3s to exit cleanly, then force
            time.sleep(0.2)
            if not os.path.exists(f"/proc/{pid}"):
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def serve(orchestrator, host: str = "127.0.0.1", port: int = DEFAULT_API_PORT,
          vault_path=None, token=None):
    try:
        orchestrator.api_port = int(port)
    except Exception:
        pass
    holder = {}

    def _shutdown():
        # serve_forever() must be stopped from a different thread than itself.
        h = holder.get("httpd")
        if h is not None:
            threading.Thread(target=h.shutdown, daemon=True).start()

    # Bind with a short retry: if a leftover supervisor from a previous (hard-killed) launch is still
    # squatting the port, reap it once and retry — SO_REUSEADDR can't bind over a LIVE listener, so a
    # relaunch would otherwise crash-loop on EADDRINUSE and hang the UI at "Starting wallets".
    httpd = None
    for attempt in range(4):
        try:
            httpd = make_server(orchestrator, host, port, vault_path, token, shutdown_cb=_shutdown)
            break
        except OSError as e:
            if e.errno != errno.EADDRINUSE or attempt == 3:
                raise
            if attempt == 0:
                _reap_foreign_supervisor(port)
            time.sleep(0.3 * (2 ** attempt))
    holder["httpd"] = httpd
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
