import os
import stat
import sys
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from unified.orchestrator import Orchestrator
import unified.orchestrator as orchestrator_mod


def make_orchestrator(tmp_path):
    return Orchestrator(
        python_bin=sys.executable,
        workspaces_root=str(tmp_path / "workspaces"),
        datadirs_root=str(tmp_path / "data"),
        servers={"BLC": "electrum1.example:50002:s"},
        coins={"BLC": {"coin_name": "Blakecoin"}},
        ports={"BLC": 57101},
    )


def test_dex_integration_defaults_to_disabled(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        assert orch.dex_integration_settings() == {
            "allow_local_dex": False,
            "start_local_dex_on_startup": False,
            "dex_connected": False,
            "dex_last_seen": None,
            "heartbeat_ttl_seconds": 45,
            "trusted_dex_id": None,
            "trusted_dex_name": None,
            "approved_at": None,
            "active_dex_id": None,
            "pending_dex_pair": None,
        }
        assert orch.dex_ready_status() == {
            "integration_allowed": False,
            "scoped_signing": False,
            "dex_connected": False,
            "dex_last_seen": None,
            "heartbeat_ttl_seconds": 45,
        }
    finally:
        orch.stop_all()


def test_dex_integration_ready_payload_is_non_secret_and_owner_only(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        settings = orch.set_dex_integration(True)
        assert settings == {
            "allow_local_dex": True,
            "start_local_dex_on_startup": False,
            "dex_connected": False,
            "dex_last_seen": None,
            "heartbeat_ttl_seconds": 45,
            "trusted_dex_id": None,
            "trusted_dex_name": None,
            "approved_at": None,
            "active_dex_id": None,
            "pending_dex_pair": None,
        }

        pending = orch.dex_ready_status("dex-test-id", "Test DEX")
        assert pending["require_approval"] is True
        assert pending["dex_instance_id"] == "dex-test-id"
        assert "dex_session_token" not in pending
        assert orch.dex_integration_settings()["pending_dex_pair"]["id"] == "dex-test-id"

        disabled = orch.set_dex_integration(False)
        assert disabled["pending_dex_pair"] is None
        orch.set_dex_integration(True)
        pending = orch.dex_ready_status("dex-test-id", "Test DEX")
        assert pending["pending_dex_pair"]["id"] == "dex-test-id"

        second = orch.dex_ready_status("other-dex-id", "Other DEX")
        assert second["require_approval"] is True
        assert second["already_pending"] is True
        assert second["dex_instance_id"] == "other-dex-id"
        assert "dex_session_token" not in second
        # Another instance's pending id must never be echoed — only its display name.
        assert "id" not in second["pending_dex_pair"]
        assert second["pending_dex_pair"]["name"] == "Test DEX"
        assert "already waiting" in second["message"]
        assert orch.dex_integration_settings()["pending_dex_pair"]["id"] == "dex-test-id"

        cleared = orch.clear_pending_dex_pairing()
        assert cleared["pending_dex_pair"] is None
        second_pending = orch.dex_ready_status("other-dex-id", "Other DEX")
        assert second_pending["pending_dex_pair"]["id"] == "other-dex-id"
        # Token-free cancel: only the exact pending id cancels, and the response is a
        # bare ack — never the settings snapshot (ids are pairing credentials).
        wrong_cancel = orch.cancel_pending_dex_pairing("dex-test-id")
        assert wrong_cancel == {"cancelled": False}
        assert orch.dex_integration_settings()["pending_dex_pair"]["id"] == "other-dex-id"
        cancelled = orch.cancel_pending_dex_pairing("other-dex-id")
        assert cancelled == {"cancelled": True}
        assert orch.dex_integration_settings()["pending_dex_pair"] is None
        orch.dex_ready_status("dex-test-id", "Test DEX")

        approved = orch.approve_dex_pairing("dex-test-id", "Test DEX")
        assert approved["trusted_dex_id"] == "dex-test-id"
        assert approved["trusted_dex_name"] == "Test DEX"

        # With a DEX paired, an unknown instance gets a 403 that must not reveal
        # the trusted identity (it doubles as the /ready pairing credential).
        rejected = orch.dex_ready_status("other-dex-id", "Other DEX")
        assert rejected["_http_status"] == 403
        assert "trusted_dex_id" not in rejected
        assert "trusted_dex_name" not in rejected
        assert "dex_session_token" not in rejected
        assert "coins" not in rejected

        ready = orch.dex_ready_status("dex-test-id", "Test DEX")
        assert ready["integration_allowed"] is True
        assert ready["scoped_signing"] is True
        assert ready["require_approval"] is False
        assert ready["dex_instance_id"] == "dex-test-id"
        assert isinstance(ready["dex_session_token"], str)
        assert len(ready["dex_session_token"]) >= 32
        assert ready["dex_connected"] is False
        assert ready["dex_last_seen"] is None
        assert ready["heartbeat_ttl_seconds"] == 45
        assert ready["locked"] is True
        assert ready["datadirs_root"] == str(tmp_path / "data")
        assert ready["coins"]["BLC"]["config_path"] == str(tmp_path / "data" / "blc" / "config")
        assert ready["coins"]["BLC"]["rpc_port"] == 57101
        assert "dex_session_token" not in ready["coins"]["BLC"]
        assert "rpc_password" not in ready["coins"]["BLC"]
        assert "password" not in ready["coins"]["BLC"]
    finally:
        orch.stop_all()


def test_dex_identity_is_sanitized_for_display(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        orch.set_dex_integration(True)
        # Control chars in the name must not reach the approval prompt.
        pending = orch.dex_ready_status("dex-test-id", "Evil\r\nDEX\x07 \x1b[31mname")
        assert pending["require_approval"] is True
        name = orch.dex_integration_settings()["pending_dex_pair"]["name"]
        assert name == "Evil DEX [31mname"
        assert not any(ord(c) < 0x20 or ord(c) == 0x7f for c in name)
        # An id carrying control chars is rejected outright (treated as missing).
        bad = orch.dex_ready_status("dex\nid", "DEX")
        assert bad["_http_status"] == 400
    finally:
        orch.stop_all()


def test_dex_startup_preference_is_persistent_and_owner_only(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        enabled = orch.set_dex_start_on_startup(True)
        assert enabled["allow_local_dex"] is True
        assert enabled["start_local_dex_on_startup"] is True

        path = tmp_path / "data" / "dex_integration.json"
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == {
                "start_local_dex_on_startup": True,
                "trusted_dex_id": None,
                "trusted_dex_name": None,
                "approved_at": None,
            }

        disabled = orch.set_dex_start_on_startup(False)
        assert disabled["allow_local_dex"] is True
        assert disabled["start_local_dex_on_startup"] is False
    finally:
        orch.stop_all()


def test_dex_heartbeat_is_volatile_and_respects_consent(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        # Disabled -> the caller is unvalidated: minimal body, no settings snapshot.
        off = orch.record_dex_heartbeat()
        assert off["dex_connected"] is False
        assert "trusted_dex_id" not in off
        assert "pending_dex_pair" not in off

        orch.set_dex_integration(True)
        orch.dex_ready_status("dex-test-id", "Test DEX")
        orch.approve_dex_pairing("dex-test-id", "Test DEX")
        token = orch.dex_ready_status("dex-test-id", "Test DEX")["dex_session_token"]
        heartbeat = orch.record_dex_heartbeat("dex-test-id", token)
        assert heartbeat["allow_local_dex"] is True
        assert heartbeat["dex_connected"] is True
        assert heartbeat["active_dex_id"] == "dex-test-id"
        assert isinstance(heartbeat["dex_last_seen"], int)

        settings = orch.dex_integration_settings()
        assert settings["dex_connected"] is True
        assert settings["dex_last_seen"] == heartbeat["dex_last_seen"]

        disabled = orch.set_dex_integration(False)
        assert disabled["dex_connected"] is False
        assert disabled["dex_last_seen"] is None
        assert disabled["active_dex_id"] is None
    finally:
        orch.stop_all()


def test_coin_change_announces_to_approved_dex_without_startup_autoconnect(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        events = []
        orch.set_dex_integration(True)
        orch.dex_ready_status("dex-test-id", "Test DEX")
        orch.approve_dex_pairing("dex-test-id", "Test DEX")
        assert orch.dex_integration_settings()["start_local_dex_on_startup"] is False

        orch._record_dex_funding_audit = lambda payload: events.append(("audit", payload))
        orch.schedule_dex_announce = lambda reason, require_startup_auto=True: events.append(
            ("announce", reason, require_startup_auto)
        )

        orch._announce_coin_change("coin-start", "PHO")
        assert events == [
            ("audit", {"event": "coin-start", "ticker": "PHO"}),
            ("announce", "coin-start", False),
        ]
    finally:
        orch.stop_all()


def test_runtime_dex_announce_requires_matching_trusted_dex_status_file(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        orch.set_dex_integration(True)
        orch.dex_ready_status("dex-test-id", "Test DEX")
        orch.approve_dex_pairing("dex-test-id", "Test DEX")

        assert orch._dex_status_matches_trusted_pair({"dex_instance_id": "dex-test-id"}) is True
        assert orch._dex_status_matches_trusted_pair({"dex_instance_id": "other-dex-id"}) is False
        assert orch._dex_status_matches_trusted_pair({}) is False
    finally:
        orch.stop_all()


def test_ready_reports_source_mode_daemon_by_rpc_not_popen_handle(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        orch.set_dex_integration(True)
        orch.dex_ready_status("dex-test-id", "Test DEX")
        orch.approve_dex_pairing("dex-test-id", "Test DEX")
        orch.status["BLC"] = "ready"
        orch.daemons["BLC"].proc = None

        def fake_drpc(ticker, method, params, timeout=60):
            assert ticker == "BLC"
            assert method == "getinfo"
            return {
                "connected": True,
                "network": "mainnet",
                "blockchain_height": 100,
                "server_height": 100,
            }

        orch._drpc = fake_drpc
        ready = orch.dex_ready_status("dex-test-id", "Test DEX")
        assert ready["coins"]["BLC"]["running"] is True
        assert ready["coins"]["BLC"]["connected"] is True
        assert ready["coins"]["BLC"]["network"] == "mainnet"
    finally:
        orch.stop_all()


def test_ready_reports_running_daemon_when_getinfo_temporarily_fails(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        orch.set_dex_integration(True)
        orch.dex_ready_status("dex-test-id", "Test DEX")
        orch.approve_dex_pairing("dex-test-id", "Test DEX")
        orch.status["BLC"] = "ready"

        def fake_drpc(ticker, method, params, timeout=60):
            raise RuntimeError("temporary getinfo failure")

        def fake_daemon_alive(ticker, timeout=10.0):
            assert ticker == "BLC"
            assert timeout == 2
            return True

        orch._drpc = fake_drpc
        orch.daemon_alive = fake_daemon_alive
        ready = orch.dex_ready_status("dex-test-id", "Test DEX")
        assert ready["coins"]["BLC"]["running"] is True
        assert ready["coins"]["BLC"]["connected"] is False
        assert ready["coins"]["BLC"]["rpc_error"] == "temporary getinfo failure"
    finally:
        orch.stop_all()


def test_start_coin_loads_wallet_when_daemon_is_already_alive(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        wallet_path = tmp_path / "data" / "blc" / "wallets" / "default_wallet"
        wallet_path.parent.mkdir(parents=True)
        wallet_path.write_bytes(b"test-wallet")
        orch.set_session_keys({"BLC": "wallet-password"}, None)
        calls = []

        def fake_daemon_alive(ticker, timeout=10.0):
            assert ticker == "BLC"
            return True

        def fake_load(ticker):
            calls.append(ticker)
            orch._loaded.add(ticker)

        orch.daemon_alive = fake_daemon_alive
        orch.daemon_accepts_current_rpc = lambda ticker, timeout=2.0: True
        orch._wallet_is_encrypted = lambda ticker: True
        orch.load = fake_load

        state = orch.start_coin("BLC")
        assert calls == ["BLC"]
        assert state["status"] == "ready"
        assert state["running"] is True
        assert state["loaded"] is True
    finally:
        orch.stop_all()


def test_start_coin_reconfigures_when_live_daemon_has_stale_rpc_password(tmp_path):
    orch = make_orchestrator(tmp_path)
    try:
        events = []

        def fake_accepts_current_rpc(ticker, timeout=2.0):
            events.append(("accepts", ticker))
            return False

        def fake_configure(ticker):
            events.append(("configure", ticker))

        def fake_start(ticker):
            events.append(("start", ticker))

        orch.daemon_accepts_current_rpc = fake_accepts_current_rpc
        orch.configure = fake_configure
        orch.start = fake_start
        orch.wait_ready = lambda ticker, timeout=45.0: True
        orch._load_started_coin_wallet = lambda ticker: events.append(("load", ticker))
        orch._synced_now = lambda ticker: True
        orch.daemon_alive = lambda ticker, timeout=10.0: True

        state = orch.start_coin("BLC")
        assert events[:4] == [
            ("accepts", "BLC"),
            ("configure", "BLC"),
            ("start", "BLC"),
            ("load", "BLC"),
        ]
        assert state["status"] == "ready"
    finally:
        orch.stop_all()


def test_dex_announce_uses_owner_only_status_file_and_minimal_payload(tmp_path, monkeypatch):
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            received["path"] = self.path
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            received["body"] = json.loads(raw.decode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    status_path = tmp_path / "dex-status.json"
    monkeypatch.setattr(orchestrator_mod, "DEX_STATUS_FILE", str(status_path))
    status_path.write_text(json.dumps({
        "host": "127.0.0.1",
        "port": server.server_port,
        "announce_path": "/integrations/electrum/announce",
        "announce_token": "x" * 40,
    }), encoding="utf-8")
    os.chmod(status_path, 0o600)

    orch = make_orchestrator(tmp_path)
    try:
        status = orch._read_dex_status_file()
        assert status is not None
        assert orch._post_dex_announce(status) is True
        assert received["path"] == "/integrations/electrum/announce"
        assert received["body"] == {
            "wallet_ready_port": 57100,
            "wallet_version": "Blakestream Wallet",
            "auto_start": True,
            "announce_token": "x" * 40,
        }
    finally:
        orch.stop_all()
        server.shutdown()
