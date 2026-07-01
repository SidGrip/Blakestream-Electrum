"""P2 daemon orchestrator (Option B MVP): drive the six per-coin Electrum daemons
from ONE master mnemonic, behind a unified API.

Each coin runs as its own headless ``electrum daemon`` process out of its generated
variant workspace (so that coin's ``constants.net`` is active — see §4). The
orchestrator:

  * derives a per-coin account ``zprv`` from the shared mnemonic
    (``provisioning.derive_account_xprv``) and restores each daemon from it,
    feeding the key over **stdin, never argv** (process-list safe);
  * configures + supervises the daemons and aggregates balances/history over the
    daemon JSON-RPC.

The mnemonic stays in this process only — daemons receive a per-coin account xprv,
never the seed (approach A; see ``blakestream-electrum.md`` §5b/§7).

Recipe: configure rpc -> start daemon -> poll ``getinfo`` until ready -> ``restore``
(account zprv via stdin) -> ``load_wallet`` -> aggregate -> ``stop``. The generated
variants ship ``servers.json`` (electrum1 + electrum2 on this coin's ports) and a
``NETWORK_SERVER`` default with ``auto_connect``, so each coin comes up online against
its own ElectrumX by default; configure() writes an explicit ``server`` only when one
is requested, and a coin starts ``--offline`` only when explicitly set offline.
"""

from __future__ import annotations

import base64
import concurrent.futures
import contextlib
import hmac
import json
import os
import re
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from typing import Dict, Optional

try:
    import fcntl  # POSIX advisory lock used to serialise daemon (re)starts
except ImportError:  # Windows: no fcntl — bring-up there is single-threaded anyway
    fcntl = None

from unified import prices, provisioning, vault

DEFAULT_RPC_PORTS = {
    "BLC": 57101, "BBTC": 57102, "ELT": 57103,
    "LIT": 57104, "PHO": 57105, "UMO": 57106,
}

# Default FIXED fee rate (sat/byte) — used in "fixed" mode and as the fallback when a coin's
# server returns no dynamic estimate. All six coins share a 1 sat/vByte relay floor
# (DEFAULT_TRANSACTION_MINFEE = 1000 sat/kvB); 10 gives a ~10x margin yet is still a tiny
# absolute fee on these low-value chains (matches the rate XLite uses for Litecoin).
DEFAULT_FIXED_FEERATE = 10
# Network-mode fallback when the server can't estimate fees. These low-traffic chains have no fee
# market, so blockchain.estimatefee returns -1 at every target; rather than fail a send with
# NoDynamicFeeEstimates, build at this low rate (just above the 1 sat/vB relay floor).
NETWORK_FALLBACK_FEERATE = 2
MIN_SEND_CONFIRMATIONS = 6
DEX_INTEGRATION_DEFAULT = {
    "allow_local_dex": False,
    "start_local_dex_on_startup": False,
    "trusted_dex_id": None,
    "trusted_dex_name": None,
    "approved_at": None,
}
DEX_HEARTBEAT_TTL_SECONDS = 45
DEX_STATUS_FILE = os.path.expanduser("~/.blakestream/dex-status.json")
DEX_ANNOUNCE_RETRY_DELAYS = (0.5, 2.0, 5.0, 15.0, 30.0, 30.0, 30.0)
DEFAULT_COIN_COLORS = {
    "BLC": "#c7a470",
    "BBTC": "#b03b30",
    "ELT": "#5ea670",
    "LIT": "#b29a53",
    "PHO": "#517bbd",
    "UMO": "#6154a6",
}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Price-source config (the user-customizable price/FX layer; see prices.py + test-api.md).
PRICE_ROLES = ("coin_btc", "btc_fiat", "coin_fiat")
PRICE_KINDS = ("http_template",)   # every source is a user-supplied named API link
_PRICE_PLACEHOLDERS = frozenset({"coin", "coin_lower", "fiat", "fiat_lower", "ids"})
_PRICE_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_PRICE_JSONPATH_RE = re.compile(r"^[A-Za-z0-9_.{}\-]*$")
_FIAT_RE = re.compile(r"^[A-Z]{3}$")
DEFAULT_POLL_SECONDS = 30     # global price-refresh cadence (user-configurable)
MIN_POLL_SECONDS = 5
MAX_POLL_SECONDS = 3600

# Health-aware auto-failover: a coin that's synced but shows 100% unconfirmed for this long is
# usually on a server whose address index is lagging — try another server (see supervise_once).
FAILOVER_STUCK_SECONDS = 20
FAILOVER_CHECK_INTERVAL = 10  # how often the supervisor evaluates the condition
FAILOVER_COOLDOWN = 600       # after a fruitless failover, leave this coin alone for 10 min
FAILOVER_SYNC_GRACE = 15      # seconds to let a freshly-switched server sync before judging it
SYNCED_POLL_INTERVAL = 8      # how often the supervisor refreshes each coin's is_synchronized flag


def _friendly_send_error(raw: str, from_coins=None) -> str:
    """Turn a daemon payto/broadcast failure (usually a full traceback whose real
    exception is buried mid-text) into a short, user-facing message. Scans the WHOLE
    output for known causes; the CLI's generic 'internal error' last line is useless.
    When ``from_coins`` is set, an insufficient-funds failure names coin control as the
    cause so the user knows their selection — not the wallet balance — is the limit."""
    low = (raw or "").lower()
    if any(s in low for s in ("notenoughfunds", "not enough funds", "insufficient")):
        if from_coins:
            return ("The coins you picked in coin control don't cover this amount plus the network "
                    "fee. Untick coins to let the wallet choose, or select more.")
        return "Insufficient funds for this amount plus the network fee."
    if any(s in low for s in ("invalidchecksum", "checksum", "not a valid", "aliasnotfound",
                              "bitcoinexception", "failed to decode", "unknown address")) \
            or ("invalid" in low and "address" in low):
        return "That doesn't look like a valid address for this coin."
    if any(s in low for s in ("not connected", "no server", "connection", "timed out", "timeout",
                              "could not connect")):
        return "Not connected to the coin's server — please try again in a moment."
    if "dust" in low:
        return "That amount is below the network's dust threshold."
    # No known pattern: surface the most exception-looking line, else a safe default.
    for ln in reversed([x.strip() for x in (raw or "").splitlines() if x.strip()]):
        if ("error" in ln.lower() or "exception" in ln.lower()) \
                and "internal error while executing" not in ln.lower():
            return ln[:200]
    return "The transaction could not be sent."


# Sentinel CoinDaemon.server value: bring this coin up ONLINE but write no explicit
# server/auto_connect, so the variant's baked NETWORK_SERVER default (electrum1) +
# auto_connect + bundled servers.json (electrum1/electrum2 failover) drive the connection.
DAEMON_DEFAULT = "__daemon_default__"


@dataclass
class CoinDaemon:
    ticker: str
    workspace: str          # generated variant source tree (its own constants.net)
    datadir: str            # ELECTRUMDIR
    rpc_port: int
    server: Optional[str] = None   # explicit "host:port:s|t"; DAEMON_DEFAULT => online via baked default; None => OFFLINE
    proxy: Optional[dict] = None   # {host, port, user, password} for a SOCKS5 proxy, or None for direct
    rpc_user: str = "electrum"
    rpc_password: str = field(default_factory=lambda: secrets.token_hex(16))
    proc: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)
    restarts: int = 0
    backoff: float = 1.0          # seconds; doubles on a failed restart, resets when healthy
    next_retry: float = 0.0       # monotonic deadline before the next restart attempt
    cmd: list = field(default_factory=list)   # exec prefix: [python, run_electrum] or [bundled-binary]
    cwd: Optional[str] = None
    bundled: bool = False


class DexOrdersActiveError(Exception):
    """Raised by stop_coin when the DEX is connected and the caller didn't pass force=True —
    stopping the coin would drop it from the DEX, so the UI must confirm first (HTTP 409)."""


class Orchestrator:
    def __init__(
        self,
        *,
        python_bin: str,
        workspaces_root: str,
        datadirs_root: str,
        servers: Dict[str, str],
        coins: Optional[Dict[str, dict]] = None,
        ports: Optional[Dict[str, int]] = None,
        oracle=None,
        binaries: Optional[Dict[str, str]] = None,
    ):
        self.python_bin = python_bin
        self.coins = coins if coins is not None else provisioning.load_coins()
        self.ports = ports or DEFAULT_RPC_PORTS
        self.oracle = oracle  # optional unified.prices.PriceOracle (duck-typed)
        self.datadirs_root = datadirs_root
        self.api_port = 57100
        self._loaded: set = set()  # coins whose wallet is loaded in its daemon this session
        # Live unlock progress, updated by provision_all (POST thread) and read by the
        # /setup/progress GET (another thread) — guarded by a lock for clean reads.
        self._progress_lock = threading.Lock()
        self._progress = {"coins": {}, "total": 0}
        self._pending: Dict[str, str] = {}  # ticker -> previewed unsigned PSBT awaiting confirm
        self._stopping = False  # set by stop_all(); vetoes the supervisor so it can't restart
                                # daemons we are deliberately tearing down (graceful shutdown)
        self._supervision_enabled = False  # supervisor stays idle until the initial bring-up
                                # completes, so it can't race startup (configure/provision)
        self.daemons: Dict[str, CoinDaemon] = {}
        # Owner-only datadirs root: it holds the encrypted vault, the (now-encrypted) contacts, and
        # the per-coin wallet files. Tighten to 0700 so other local users can't even list it.
        try:
            os.makedirs(datadirs_root, exist_ok=True)
            os.chmod(datadirs_root, 0o700)
        except OSError:
            pass
        for ticker in self.coins:
            if ticker not in self.ports:     # manage every coin we have an RPC port for
                continue
            server = servers.get(ticker)     # None => this coin runs offline (no ElectrumX yet)
            ws = os.path.join(workspaces_root, ticker)
            if binaries and ticker in binaries:           # packaged: standalone binary
                binary = binaries[ticker]
                cmd, cwd, bundled = [binary], (os.path.dirname(binary) or None), True
            else:                                          # dev: python + the variant workspace
                cmd, cwd, bundled = [python_bin, os.path.join(ws, "run_electrum")], ws, False
            self.daemons[ticker] = CoinDaemon(
                ticker=ticker, workspace=ws,
                datadir=os.path.join(datadirs_root, ticker.lower()),
                rpc_port=self.ports[ticker], server=server,
                cmd=cmd, cwd=cwd, bundled=bundled,
            )
        # per-coin startup state for the UI progress screen: pending -> starting -> ready/failed
        self.status: Dict[str, str] = {t: "pending" for t in self.daemons}
        # Windows Job Object (created lazily): every daemon Popen is assigned to a job with
        # KILL_ON_JOB_CLOSE so the OS kills the whole daemon tree the instant THIS supervisor exits
        # (clean quit, crash, or taskkill) — making orphaned per-coin daemons impossible on Windows,
        # the root cause of the reopen hang. persist+reaper stays as the cross-platform fallback.
        self._win_job = None
        # Per-coin transaction-fee policy: 'network' (dynamic estimate, falling back to the
        # fixed rate when the server can't estimate) or 'fixed' (always the saved sat/byte).
        # Persisted in a 0600 sidecar in the datadirs root; seeded network/DEFAULT_FIXED_FEERATE.
        self._fees_path = os.path.join(datadirs_root, "fees.json")
        self._fees_lock = threading.Lock()
        self._fees = self._load_fees()
        self._dex_integration_path = os.path.join(datadirs_root, "dex_integration.json")
        self._dex_funding_audit_path = os.path.join(datadirs_root, "dex_funding_audit.jsonl")
        self._dex_integration_lock = threading.Lock()
        self._dex_integration = self._load_dex_integration()
        self._dex_session_token = secrets.token_urlsafe(32)
        self._dex_last_seen_monotonic = None
        self._dex_last_seen_unix = None
        self._dex_active_id = None
        self._dex_pending_pair = None
        self._dex_announce_lock = threading.Lock()
        self._dex_announce_inflight = False
        self._coin_colors_path = os.path.join(datadirs_root, "coin_colors.json")
        self._coin_colors_lock = threading.Lock()
        self._coin_colors = self._load_coin_colors()
        # Which coins auto-start at launch: include_all (today's behavior) or a saved subset.
        # Persisted 0600 beside coin_colors.json; a missing file => start all (backward-compatible).
        # ``self._active`` is the set of coins MEANT to be running this session — it gates the
        # supervisor's auto-restart and is updated by start_coin/stop_coin.
        self._autostart_path = os.path.join(datadirs_root, "autostart_coins.json")
        self._autostart_lock = threading.Lock()
        self._autostart = self._load_autostart()
        self._active: set = set()
        # Per-coin Lightning HUB: a well-known always-on LN node (node_id@host:port) the wallet
        # auto-connects to on bring-up, so users land already peered to a node they can open a
        # channel to (and route through). Read from an ``ln_hubs.json`` sidecar (admin/infra-set;
        # overrides any baked coins.json "ln_hub"); empty until the hubs are deployed.
        self._ln_hubs_path = os.path.join(datadirs_root, "ln_hubs.json")
        self._ln_hubs = self._load_ln_hubs()
        self._ln_hub_connected: set = set()   # coins whose hub we've connected this session
        # User-configurable price sources (CoinGecko/CMC/exchange/manual) + display fiat,
        # in a 0600 sidecar beside fees.json. The oracle + FX bridge are rebuilt from it; a
        # daemon thread warms a price snapshot so portfolio() never does network in the request
        # thread. OFF until the user enables a source (then values appear). An injected oracle
        # (tests) is left untouched.
        self._prices_path = os.path.join(datadirs_root, "price_sources.json")
        self._prices_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._price_snapshot = None       # {"fiat": code, "units": {ticker: Decimal|None}}
        self._fx = None                   # prices.FrankfurterFx, rebuilt with the oracle
        self._injected_oracle = oracle is not None
        self._prices = self._load_price_sources()
        if not self._injected_oracle:
            self._build_oracle()
        self._price_thread = threading.Thread(
            target=self._price_refresh_loop, name="price-refresh", daemon=True)
        self._price_thread.start()
        # Health-aware auto-failover state (per coin): detect a "synced but 100% unconfirmed"
        # coin (lagging-index server) and switch it to a healthier server. See supervise_once.
        self._failover = {t: {"stuck_since": None, "tried": set(),
                              "cooldown_until": 0.0, "in_flight": False}
                          for t in self.daemons}
        self._failover_locks = {t: threading.Lock() for t in self.daemons}
        self._last_failover_check = 0.0
        # Per-coin is_synchronized, refreshed by the supervisor so portfolio() (network-free) can
        # tell "still syncing" (show 'syncing', not 'pending') from a genuine 0-conf balance.
        self._synced_cache = {t: None for t in self.daemons}   # True | False | None(unknown)
        # At-rest encryption: per-coin wallet-encryption passwords + a contacts key, derived from
        # the seed and held in memory ONLY for the unlocked session (cleared by stop_all). Empty
        # until the unlock handler calls set_session_keys, so dev / pre-unlock stays plaintext.
        self._wallet_pws: Dict[str, str] = {}
        self._contacts_key: Optional[bytes] = None
        self._last_synced_poll = 0.0

    def is_online(self, ticker: str) -> bool:
        return self.daemons[ticker].server is not None

    def startup_status(self) -> dict:
        """Live per-coin bring-up progress (polled by the Connecting screen). Coins the user
        deliberately did not start are "stopped" and excluded from the ready/total math, so the
        overlay's all-ready gate never waits on a coin that was never meant to start."""
        st = dict(self.status)
        active = [t for t in st if st[t] != "stopped"]
        settled = sum(1 for t in active if st[t] in ("ready", "failed"))
        return {
            "coins": st,
            "ready": sum(1 for t in active if st[t] == "ready"),
            "total": len(active),
            "all_ready": bool(active) and settled == len(active),
            "stopped": [t for t in st if st[t] == "stopped"],
        }

    def _load_dex_integration(self) -> dict:
        settings = dict(DEX_INTEGRATION_DEFAULT)
        try:
            with open(self._dex_integration_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return settings
        if isinstance(saved, dict):
            # Migration: old builds persisted allow_local_dex directly. New builds
            # treat allow_local_dex as runtime-only and persist only the startup flag.
            start_on_startup = bool(saved.get("start_local_dex_on_startup", saved.get("allow_local_dex")))
            settings["start_local_dex_on_startup"] = start_on_startup
            settings["allow_local_dex"] = start_on_startup
            trusted_dex_id = str(saved.get("trusted_dex_id") or "").strip()
            trusted_dex_name = str(saved.get("trusted_dex_name") or "").strip()
            if trusted_dex_id:
                settings["trusted_dex_id"] = trusted_dex_id
                settings["trusted_dex_name"] = trusted_dex_name[:80] or "Blakestream DEX"
            approved_at = saved.get("approved_at")
            if isinstance(approved_at, int) and approved_at > 0:
                settings["approved_at"] = approved_at
        return settings

    def _save_dex_integration(self) -> None:
        tmp = self._dex_integration_path + ".tmp"
        saved = {
            "start_local_dex_on_startup": bool(self._dex_integration.get("start_local_dex_on_startup")),
            "trusted_dex_id": self._dex_integration.get("trusted_dex_id"),
            "trusted_dex_name": self._dex_integration.get("trusted_dex_name"),
            "approved_at": self._dex_integration.get("approved_at"),
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._dex_integration_path)

    def _normalize_coin_colors(self, colors) -> dict:
        normalized = dict(DEFAULT_COIN_COLORS)
        if isinstance(colors, dict):
            for ticker, color in colors.items():
                t = str(ticker or "").upper().strip()
                c = str(color or "").strip()
                if t and _HEX_COLOR_RE.match(c):
                    normalized[t] = c.lower()
        return normalized

    def _load_coin_colors(self) -> dict:
        try:
            with open(self._coin_colors_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return self._normalize_coin_colors({})
        return self._normalize_coin_colors(saved)

    def _save_coin_colors(self) -> None:
        tmp = self._coin_colors_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._coin_colors, f, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._coin_colors_path)

    def coin_colors(self) -> dict:
        with self._coin_colors_lock:
            return {"colors": dict(self._coin_colors)}

    def set_coin_colors(self, colors) -> dict:
        with self._coin_colors_lock:
            self._coin_colors = self._normalize_coin_colors(colors)
            self._save_coin_colors()
            return {"colors": dict(self._coin_colors)}

    def _load_autostart(self) -> dict:
        pref = {"include_all": True, "coins": set()}
        try:
            with open(self._autostart_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return pref
        if isinstance(saved, dict):
            pref["include_all"] = bool(saved.get("include_all", True))
            coins = saved.get("coins") or []
            if isinstance(coins, (list, tuple)):
                pref["coins"] = {str(t).upper().strip() for t in coins
                                 if str(t).upper().strip() in self.daemons}
        return pref

    def _save_autostart(self) -> None:
        tmp = self._autostart_path + ".tmp"
        saved = {"include_all": bool(self._autostart["include_all"]),
                 "coins": sorted(self._autostart["coins"])}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f, sort_keys=True)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._autostart_path)

    def autostart_settings(self) -> dict:
        with self._autostart_lock:
            return {
                "include_all": bool(self._autostart["include_all"]),
                "coins": sorted(self._autostart["coins"]),
                "available": list(self.daemons),
            }

    def set_autostart(self, include_all, coins) -> dict:
        """Persist the autostart preference. Takes effect NEXT launch; does NOT start/stop
        daemons now. An empty subset with include_all False is coerced to 'start all' so a
        zero-coin startup is never persisted."""
        with self._autostart_lock:
            inc = bool(include_all)
            chosen = {str(t).upper().strip() for t in (coins or [])
                      if str(t).upper().strip() in self.daemons}
            if not inc and not chosen:
                inc = True
            self._autostart = {"include_all": inc, "coins": chosen}
            self._save_autostart()
            return {"include_all": inc, "coins": sorted(chosen), "available": list(self.daemons)}

    def active_tickers(self) -> list:
        """Resolve the autostart preference to the concrete coin set to bring up at launch.
        Falls back to all coins if the saved subset is empty/unknown (defensive)."""
        with self._autostart_lock:
            if self._autostart["include_all"]:
                return list(self.daemons)
            chosen = self._autostart["coins"]
            return [t for t in self.daemons if t in chosen] or list(self.daemons)

    def _dex_connected_locked(self) -> bool:
        if not self._dex_integration.get("allow_local_dex"):
            return False
        if self._dex_last_seen_monotonic is None:
            return False
        return (time.monotonic() - self._dex_last_seen_monotonic) <= DEX_HEARTBEAT_TTL_SECONDS

    @staticmethod
    def _normalize_dex_id(value) -> str:
        dex_id = str(value or "").strip()
        if not dex_id or len(dex_id) > 128:
            return ""
        return dex_id

    @staticmethod
    def _normalize_dex_name(value) -> str:
        dex_name = str(value or "").strip()
        return dex_name[:80] or "Blakestream DEX"

    def _is_trusted_dex_locked(self, dex_id: Optional[str]) -> bool:
        dex_id = self._normalize_dex_id(dex_id)
        trusted = self._normalize_dex_id(self._dex_integration.get("trusted_dex_id"))
        return bool(dex_id and trusted and hmac.compare_digest(dex_id, trusted))

    def _pending_dex_pair_snapshot_locked(self) -> Optional[dict]:
        pending = self._dex_pending_pair
        if not isinstance(pending, dict):
            return None
        dex_id = self._normalize_dex_id(pending.get("id"))
        if not dex_id:
            return None
        first_seen = pending.get("first_seen")
        return {
            "id": dex_id,
            "name": self._normalize_dex_name(pending.get("name")),
            "first_seen": first_seen if isinstance(first_seen, int) else None,
        }

    def _set_pending_dex_pair_locked(self, dex_id: str, dex_name: Optional[str]) -> Optional[dict]:
        dex_id = self._normalize_dex_id(dex_id)
        if not dex_id:
            return self._pending_dex_pair_snapshot_locked()
        existing = self._dex_pending_pair if isinstance(self._dex_pending_pair, dict) else {}
        existing_id = self._normalize_dex_id(existing.get("id"))
        if existing_id and existing_id != dex_id:
            return self._pending_dex_pair_snapshot_locked()
        first_seen = existing.get("first_seen") if existing.get("id") == dex_id else int(time.time())
        self._dex_pending_pair = {
            "id": dex_id,
            "name": self._normalize_dex_name(dex_name),
            "first_seen": first_seen,
        }
        return self._pending_dex_pair_snapshot_locked()

    def _validate_dex_identity_locked(self, dex_id: Optional[str], dex_session_token: str) -> str:
        dex_id = self._normalize_dex_id(dex_id)
        if not dex_id:
            raise PermissionError("DEX identity is required. Reconnect Electrum Multiwallet from Blakestream DEX.")
        if not self._is_trusted_dex_locked(dex_id):
            raise PermissionError("unknown DEX instance. Approve this DEX in Electrum Multiwallet settings.")
        if self._dex_active_id and self._dex_active_id != dex_id and self._dex_connected_locked():
            raise PermissionError("another DEX is already connected")
        if not hmac.compare_digest(str(dex_session_token or ""), self._dex_session_token):
            raise PermissionError("invalid DEX session")
        self._dex_active_id = dex_id
        return dex_id

    def _dex_integration_snapshot_locked(self) -> dict:
        allowed = bool(self._dex_integration.get("allow_local_dex"))
        connected = self._dex_connected_locked()
        return {
            "allow_local_dex": allowed,
            "start_local_dex_on_startup": bool(self._dex_integration.get("start_local_dex_on_startup")),
            "dex_connected": connected,
            "dex_last_seen": self._dex_last_seen_unix if allowed else None,
            "heartbeat_ttl_seconds": DEX_HEARTBEAT_TTL_SECONDS,
            "trusted_dex_id": self._normalize_dex_id(self._dex_integration.get("trusted_dex_id")) or None,
            "trusted_dex_name": self._dex_integration.get("trusted_dex_name") or None,
            "approved_at": self._dex_integration.get("approved_at"),
            "active_dex_id": self._dex_active_id if connected else None,
            "pending_dex_pair": self._pending_dex_pair_snapshot_locked(),
        }

    def _rotate_dex_session_token_locked(self) -> None:
        self._dex_session_token = secrets.token_urlsafe(32)

    def dex_integration_settings(self) -> dict:
        with self._dex_integration_lock:
            snapshot = self._dex_integration_snapshot_locked()
            return snapshot

    def approve_dex_pairing(self, dex_id, dex_name=None) -> dict:
        dex_id = self._normalize_dex_id(dex_id)
        if not dex_id:
            raise ValueError("dex_id is required")
        should_announce = False
        with self._dex_integration_lock:
            self._dex_integration["allow_local_dex"] = True
            self._dex_integration["trusted_dex_id"] = dex_id
            self._dex_integration["trusted_dex_name"] = self._normalize_dex_name(dex_name)
            self._dex_integration["approved_at"] = int(time.time())
            self._rotate_dex_session_token_locked()
            self._dex_pending_pair = None
            self._dex_active_id = None
            self._dex_last_seen_monotonic = None
            self._dex_last_seen_unix = None
            self._save_dex_integration()
            should_announce = bool(self._dex_integration.get("start_local_dex_on_startup"))
            after = self._dex_integration_snapshot_locked()
        if should_announce:
            self.schedule_dex_announce("dex-approved")
        return after

    def forget_dex_pairing(self) -> dict:
        with self._dex_integration_lock:
            self._dex_integration["trusted_dex_id"] = None
            self._dex_integration["trusted_dex_name"] = None
            self._dex_integration["approved_at"] = None
            self._rotate_dex_session_token_locked()
            self._dex_pending_pair = None
            self._dex_active_id = None
            self._dex_last_seen_monotonic = None
            self._dex_last_seen_unix = None
            self._save_dex_integration()
            return self._dex_integration_snapshot_locked()

    def clear_pending_dex_pairing(self) -> dict:
        with self._dex_integration_lock:
            self._dex_pending_pair = None
            return self._dex_integration_snapshot_locked()

    def cancel_pending_dex_pairing(self, dex_id) -> dict:
        dex_id = self._normalize_dex_id(dex_id)
        with self._dex_integration_lock:
            pending = self._pending_dex_pair_snapshot_locked()
            pending_id = self._normalize_dex_id((pending or {}).get("id"))
            if dex_id and pending_id and hmac.compare_digest(dex_id, pending_id):
                self._dex_pending_pair = None
            return self._dex_integration_snapshot_locked()

    def set_dex_integration(self, allow_local_dex) -> dict:
        should_announce = False
        with self._dex_integration_lock:
            self._dex_integration["allow_local_dex"] = bool(allow_local_dex)
            if not self._dex_integration["allow_local_dex"]:
                self._rotate_dex_session_token_locked()
                self._dex_last_seen_monotonic = None
                self._dex_last_seen_unix = None
                self._dex_active_id = None
                self._dex_pending_pair = None
            else:
                should_announce = bool(self._dex_integration.get("start_local_dex_on_startup"))
            after = self._dex_integration_snapshot_locked()
        if should_announce:
            self.schedule_dex_announce("integration-enabled")
        return after

    def set_dex_start_on_startup(self, start_local_dex_on_startup) -> dict:
        should_announce = False
        with self._dex_integration_lock:
            start = bool(start_local_dex_on_startup)
            self._dex_integration["start_local_dex_on_startup"] = start
            if start:
                self._dex_integration["allow_local_dex"] = True
                should_announce = True
            self._save_dex_integration()
            after = self._dex_integration_snapshot_locked()
        if should_announce:
            self.schedule_dex_announce("startup-enabled")
        return after

    def record_dex_heartbeat(self, dex_instance_id=None, dex_session_token=None) -> dict:
        with self._dex_integration_lock:
            if not self._dex_integration.get("allow_local_dex"):
                snapshot = self._dex_integration_snapshot_locked()
                return snapshot
            self._validate_dex_identity_locked(dex_instance_id, dex_session_token)
            self._dex_last_seen_monotonic = time.monotonic()
            self._dex_last_seen_unix = int(time.time())
            after = self._dex_integration_snapshot_locked()
            return after

    def _dex_auto_connect_enabled(self) -> bool:
        with self._dex_integration_lock:
            return bool(self._dex_integration.get("allow_local_dex")
                        and self._dex_integration.get("start_local_dex_on_startup"))

    def _dex_announce_allowed(self, require_startup_auto: bool = True) -> bool:
        with self._dex_integration_lock:
            if not self._dex_integration.get("allow_local_dex"):
                return False
            if require_startup_auto:
                return bool(self._dex_integration.get("start_local_dex_on_startup"))
            return bool(self._normalize_dex_id(self._dex_integration.get("trusted_dex_id")))

    def _read_dex_status_file(self) -> Optional[dict]:
        try:
            st = os.stat(DEX_STATUS_FILE)
            if os.name != "nt" and (st.st_mode & 0o077):
                return None
            with open(DEX_STATUS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        host = str(data.get("host") or "127.0.0.1").strip()
        port = data.get("port")
        path = str(data.get("announce_path") or "/integrations/electrum/announce").strip()
        token = str(data.get("announce_token") or "").strip()
        if host not in ("127.0.0.1", "localhost"):
            return None
        if not isinstance(port, int) or port < 1 or port > 65535:
            return None
        if not path.startswith("/") or "://" in path:
            return None
        if len(token) < 32:
            return None
        dex_instance_id = self._normalize_dex_id(data.get("dex_instance_id"))
        dex_name = self._normalize_dex_name(data.get("dex_name"))
        return {
            "host": host,
            "port": port,
            "path": path,
            "announce_token": token,
            "dex_instance_id": dex_instance_id,
            "dex_name": dex_name,
        }

    def _dex_status_matches_trusted_pair(self, status: dict) -> bool:
        status_dex_id = self._normalize_dex_id(status.get("dex_instance_id"))
        if not status_dex_id:
            return False
        with self._dex_integration_lock:
            trusted_dex_id = self._normalize_dex_id(self._dex_integration.get("trusted_dex_id"))
        return bool(trusted_dex_id and hmac.compare_digest(status_dex_id, trusted_dex_id))

    def _post_dex_announce(self, status: dict) -> bool:
        url = f"http://127.0.0.1:{int(status['port'])}{status['path']}"
        payload = json.dumps({
            "wallet_ready_port": int(getattr(self, "api_port", 57100) or 57100),
            "wallet_version": "Blakestream Wallet",
            "auto_start": True,
            "announce_token": status["announce_token"],
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= int(resp.status) < 300
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return False

    def schedule_dex_announce(self, reason: str = "", require_startup_auto: bool = True) -> None:
        if not self._dex_announce_allowed(require_startup_auto=require_startup_auto):
            return
        with self._dex_announce_lock:
            if self._dex_announce_inflight:
                return
            self._dex_announce_inflight = True

        def _worker():
            try:
                for delay in DEX_ANNOUNCE_RETRY_DELAYS:
                    if not self._dex_announce_allowed(require_startup_auto=require_startup_auto) or self._stopping:
                        return
                    time.sleep(delay)
                    status = self._read_dex_status_file()
                    if not status:
                        continue
                    if not require_startup_auto and not self._dex_status_matches_trusted_pair(status):
                        continue
                    if self._post_dex_announce(status):
                        return
            finally:
                with self._dex_announce_lock:
                    self._dex_announce_inflight = False

        threading.Thread(target=_worker, name=f"dex-announce-{reason or 'startup'}", daemon=True).start()

    def dex_ready_status(self, dex_instance_id=None, dex_name=None) -> dict:
        dex_id = self._normalize_dex_id(dex_instance_id)
        dex_display_name = self._normalize_dex_name(dex_name)
        with self._dex_integration_lock:
            settings = self._dex_integration_snapshot_locked()
            allowed = bool(settings.get("allow_local_dex"))
            if not allowed:
                result = {
                    "integration_allowed": False,
                    "scoped_signing": False,
                    "dex_connected": False,
                    "dex_last_seen": None,
                    "heartbeat_ttl_seconds": DEX_HEARTBEAT_TTL_SECONDS,
                }
                return result
            if not dex_id:
                return {
                    "integration_allowed": True,
                    "scoped_signing": False,
                    "require_approval": False,
                    "dex_connected": False,
                    "error": "DEX identity is required",
                    "_http_status": 400,
                }
            if not self._is_trusted_dex_locked(dex_id):
                if not self._dex_integration.get("trusted_dex_id"):
                    pending = self._set_pending_dex_pair_locked(dex_id, dex_display_name)
                    pending_id = self._normalize_dex_id((pending or {}).get("id"))
                    message = "Approve this DEX in Electrum Multiwallet settings."
                    if pending_id and pending_id != dex_id:
                        pending_name = self._normalize_dex_name((pending or {}).get("name"))
                        message = (
                            f"{pending_name} is already waiting for approval. "
                            "Clear it in Electrum Multiwallet settings before approving a different DEX."
                        )
                    return {
                        "integration_allowed": True,
                        "scoped_signing": False,
                        "require_approval": True,
                        "already_pending": bool(pending_id and pending_id != dex_id),
                        "dex_instance_id": dex_id,
                        "dex_name": dex_display_name,
                        "pending_dex_pair": pending,
                        "message": message,
                    }
                return {
                    "integration_allowed": True,
                    "scoped_signing": False,
                    "require_approval": False,
                    "dex_connected": settings.get("dex_connected"),
                    "trusted_dex_id": settings.get("trusted_dex_id"),
                    "trusted_dex_name": settings.get("trusted_dex_name"),
                    "error": "A different DEX is already paired. Forget the paired DEX before connecting another one.",
                    "_http_status": 403,
                }
            dex_session_token = self._dex_session_token

        coins = {}
        coin_colors = self.coin_colors().get("colors", {})
        for ticker, d in self.daemons.items():
            info = {}
            rpc_error = None
            status = self.status.get(ticker)
            running = False
            if status not in ("stopped", "stopping"):
                try:
                    info = self._drpc(ticker, "getinfo", {}, timeout=2) or {}
                    running = True
                except Exception as e:
                    rpc_error = str(e)[:120]
                    try:
                        running = self.daemon_alive(ticker, timeout=2)
                    except Exception:
                        running = False
            coin = self.coins.get(ticker, {})
            network = (
                info.get("network") if isinstance(info, dict) else None
            ) or (
                info.get("chain") if isinstance(info, dict) else None
            ) or None
            coins[ticker] = {
                "ticker": ticker,
                "coin_name": coin.get("coin_name"),
                "config_path": os.path.join(d.datadir, "config"),
                "data_dir": d.datadir,
                "rpc_host": "127.0.0.1",
                "rpc_port": d.rpc_port,
                "rpc_user": d.rpc_user,
                "network": network,
                "coin_color": coin_colors.get(ticker),
                "connected": bool(isinstance(info, dict) and info.get("connected")),
                "running": running,
                "status": status,
                "rpc_error": rpc_error,
            }
        result = {
            "integration_allowed": True,
            "product": "Blakestream Electrum Multiwallet",
            "version": "0.25.2",
            "multiwallet": True,
            "scoped_signing": True,
            "require_approval": False,
            "dex_instance_id": dex_id,
            "dex_name": settings.get("trusted_dex_name") or dex_display_name,
            "dex_session_token": dex_session_token,
            "dex_connected": bool(settings.get("dex_connected")),
            "dex_last_seen": settings.get("dex_last_seen"),
            "heartbeat_ttl_seconds": settings.get("heartbeat_ttl_seconds"),
            "locked": self.locked(),
            "datadirs_root": self.datadirs_root,
            "coins": coins,
        }
        return result

    def _dex_amount_to_sats(self, amount) -> int:
        text = str(amount or "").strip()
        if not text or text == "!":
            raise ValueError("enter an exact positive amount")
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError("enter an exact positive amount")
        if not value.is_finite() or value <= 0:
            raise ValueError("enter an exact positive amount")
        sats_value = value * Decimal(100_000_000)
        if sats_value != sats_value.to_integral_value():
            raise ValueError("amount must have no more than 8 decimal places")
        return int(sats_value)

    @staticmethod
    def _sats_to_amount_string(sats: int) -> str:
        return f"{sats // 100_000_000}.{sats % 100_000_000:08d}"

    def _tx_amount_to_address(self, ticker: str, tx: str, dest_address: str) -> int:
        j = self.rpc(ticker, "deserialize", tx)
        if not isinstance(j, dict):
            raise RuntimeError("could not read the transaction")
        try:
            return sum(int(o.get("value_sats") or 0) for o in (j.get("outputs") or [])
                       if (o.get("address") or "").lower() == dest_address.lower())
        except (TypeError, ValueError, AttributeError):
            raise RuntimeError("could not read the transaction")

    @staticmethod
    def _dex_trace_id(value) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        if not re.match(r"^[A-Za-z0-9._:-]{1,96}$", text):
            return None
        return text

    @staticmethod
    def _dex_description(value) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:180] or None

    def _set_label_best_effort(self, ticker: str, key: str, label: Optional[str]) -> None:
        if not key or not label:
            return
        try:
            self.set_label(ticker, key, label)
        except Exception:
            pass

    def _record_dex_funding_audit(self, entry: dict) -> None:
        row = {
            "time": int(time.time()),
            **entry,
        }
        try:
            fd = os.open(self._dex_funding_audit_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            pass

    def dex_fund_htlc(
        self,
        ticker: str,
        address: str,
        amount,
        dex_session_token: str,
        rpc_password: str,
        dex_instance_id: Optional[str] = None,
        *,
        swap_id=None,
        order_id=None,
        description=None,
    ) -> dict:
        """Scoped local DEX funding endpoint: fund exactly one HTLC address.

        The DEX never receives the wallet unlock password. It proves that it imported this
        session's /ready payload (session token) and the owner-only per-coin RPC config
        (RPC password), then the already-unlocked multiwallet signs internally.
        """
        ticker = str(ticker or "").upper().strip()
        if not ticker or ticker not in self.daemons:
            raise KeyError(ticker or "unknown")
        with self._dex_integration_lock:
            if not self._dex_integration.get("allow_local_dex"):
                raise PermissionError("local DEX integration disabled")
            dex_instance_id = self._validate_dex_identity_locked(dex_instance_id, dex_session_token)
        expected_rpc_password = self.daemons[ticker].rpc_password
        if not hmac.compare_digest(str(rpc_password or ""), str(expected_rpc_password or "")):
            raise PermissionError("invalid DEX RPC credential")
        if self.locked():
            raise PermissionError("unlock the Electrum Multiwallet before starting swaps")
        wallet_pw = self._wallet_pw(ticker)
        if not wallet_pw:
            raise PermissionError(f"unlock {ticker} before starting swaps")
        if not self.validate_address(ticker, address):
            raise ValueError(f"not a valid {ticker} address")
        expected_sats = self._dex_amount_to_sats(amount)
        amount_text = self._sats_to_amount_string(expected_sats)
        d, address, amount_text = self._validate_send(ticker, address, amount_text)
        feerate = self._effective_feerate(ticker)
        psbt = self._build_payto(d, ticker, address, amount_text, feerate)
        _fee_sat, amount_sat = self._psbt_fee_and_amount(ticker, psbt, address)
        if amount_sat != expected_sats:
            raise RuntimeError("wallet built a transaction with the wrong HTLC amount")
        try:
            signed = self._drpc(ticker, "signtransaction", {"tx": psbt, "password": wallet_pw})
        except Exception as e:
            raise RuntimeError(_friendly_send_error(str(e)))
        if not isinstance(signed, str) or not signed:
            raise RuntimeError("could not sign the transaction")
        signed_amount_sat = self._tx_amount_to_address(ticker, signed, address)
        if signed_amount_sat != expected_sats:
            raise RuntimeError("signed transaction has the wrong HTLC amount")
        b = self._run(d, "broadcast", signed, timeout=60)
        if b.returncode != 0:
            raise RuntimeError(_friendly_send_error((b.stderr or "") + "\n" + (b.stdout or "")))
        txid = (b.stdout or "").strip()
        if not txid:
            raise RuntimeError("transaction was built but the broadcast did not return a txid")
        label = self._dex_description(description)
        self._set_label_best_effort(ticker, txid, label)
        self._record_dex_funding_audit({
            "action": "dex_fund_htlc",
            "ticker": ticker,
            "address": address,
            "amount": amount_text,
            "amount_sat": expected_sats,
            "txid": txid,
            "dex_instance_id": dex_instance_id,
            **({"description": label} if label else {}),
            **({"swap_id": self._dex_trace_id(swap_id)} if self._dex_trace_id(swap_id) else {}),
            **({"order_id": self._dex_trace_id(order_id)} if self._dex_trace_id(order_id) else {}),
        })
        return {"txid": txid}

    def _authorize_dex_coin(
        self,
        ticker: str,
        dex_session_token: str,
        rpc_password: str,
        dex_instance_id: Optional[str] = None,
    ) -> str:
        ticker = str(ticker or "").upper().strip()
        if not ticker or ticker not in self.daemons:
            raise KeyError(ticker or "unknown")
        with self._dex_integration_lock:
            if not self._dex_integration.get("allow_local_dex"):
                raise PermissionError("local DEX integration disabled")
            self._validate_dex_identity_locked(dex_instance_id, dex_session_token)
        expected_rpc_password = self.daemons[ticker].rpc_password
        if not hmac.compare_digest(str(rpc_password or ""), str(expected_rpc_password or "")):
            raise PermissionError("invalid DEX RPC credential")
        locked = self.locked()
        wallet_pw = self._wallet_pw(ticker)
        if self.locked():
            raise PermissionError("unlock the Electrum Multiwallet before starting swaps")
        if not wallet_pw:
            raise PermissionError(f"unlock {ticker} before starting swaps")
        return ticker

    @staticmethod
    def _optional_positive_int(value, name: str) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a positive integer")
        if parsed <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return parsed

    def dex_pay_lightning(
        self,
        ticker: str,
        invoice: str,
        dex_session_token: str,
        rpc_password: str,
        dex_instance_id: Optional[str] = None,
        *,
        timeout=None,
        max_cltv=None,
        max_fee_msat=None,
        swap_id=None,
        order_id=None,
        description=None,
    ) -> dict:
        """Scoped local DEX Lightning payment endpoint.

        Mirrors ``dex_fund_htlc``: the DEX proves it imported this wallet session
        but never receives the per-coin wallet unlock password. The already-unlocked
        multiwallet pays internally using its live session key.
        """
        ticker = self._authorize_dex_coin(ticker, dex_session_token, rpc_password, dex_instance_id)
        invoice = str(invoice or "").strip()
        self._ln_guard(invoice)
        if not re.match(r"^ln[a-z0-9]{8,}$", invoice, re.IGNORECASE):
            raise ValueError("Lightning invoice must be a BOLT11 string")
        options = {
            "timeout": self._optional_positive_int(timeout, "timeout"),
            "max_cltv": self._optional_positive_int(max_cltv, "max_cltv"),
            "max_fee_msat": self._optional_positive_int(max_fee_msat, "max_fee_msat"),
        }
        result = self.ln_pay(
            ticker,
            invoice,
            timeout=options["timeout"],
            max_cltv=options["max_cltv"],
            max_fee_msat=options["max_fee_msat"],
        )
        if isinstance(result, dict) and result.get("success") is False:
            result = dict(result)
            if not result.get("failure_reason"):
                result["failure_reason"] = (
                    "Unknown Lightning payment failure; Electrum returned "
                    "success=false without a failure reason."
                )
            diagnostics = {
                "swap_id": swap_id,
                "order_id": order_id,
                "ticker": ticker,
                "payment_hash": result.get("payment_hash"),
                "log_length": len(result.get("log") or []),
            }
            for name, probe in (
                ("decoded_invoice", lambda: self.ln_decode(ticker, invoice)),
                ("status", lambda: self.ln_status(ticker)),
                ("channels", lambda: self.ln_list_channels(ticker)),
            ):
                try:
                    diagnostics[name] = probe()
                except Exception as e:
                    diagnostics[f"{name}_error"] = repr(e)
            result["diagnostics"] = diagnostics
        label = self._dex_description(description)
        if isinstance(result, dict):
            payment_hash = str(result.get("payment_hash") or "").strip()
            if re.match(r"^[0-9a-fA-F]{64}$", payment_hash):
                self._set_label_best_effort(ticker, payment_hash, label)
        self._record_dex_funding_audit({
            "action": "dex_pay_lightning",
            "ticker": ticker,
            **({"description": label} if label else {}),
            **({"swap_id": self._dex_trace_id(swap_id)} if self._dex_trace_id(swap_id) else {}),
            **({"order_id": self._dex_trace_id(order_id)} if self._dex_trace_id(order_id) else {}),
        })
        return {"result": result}

    def _env(self, d: CoinDaemon) -> dict:
        # ELECTRUMDIR points the daemon at this coin's own datadir. Some Electrum forks rename the
        # var to ELECTRUM<TICKER>_DIR and ignore ELECTRUMDIR, so set that too — harmless for forks
        # that read ELECTRUMDIR, and keeps every coin's daemon isolated to its own per-coin datadir.
        env = dict(os.environ, ELECTRUMDIR=d.datadir)
        env[f"ELECTRUM{d.ticker.upper()}_DIR"] = d.datadir
        if not d.bundled:                       # source mode needs the variant on the path
            py_path = [d.workspace]
            if env.get("PYTHONPATH"):
                py_path.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(py_path)
        return env

    # -- low-level: run an electrum command for one coin (its own net) --
    def _run(self, d: CoinDaemon, *args, online: bool = True,
             stdin: Optional[str] = None, timeout: int = 60) -> subprocess.CompletedProcess:
        cmd = list(d.cmd)
        if not online:
            cmd.append("--offline")
        cmd += [str(a) for a in args]
        return subprocess.run(cmd, cwd=d.cwd, env=self._env(d), input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def _checked(self, d: CoinDaemon, *args, **kw):
        r = self._run(d, *args, **kw)
        if r.returncode != 0:
            raise RuntimeError(f"{d.ticker} {' '.join(map(str, args))} failed: "
                               f"{(r.stderr or r.stdout).strip()[:200]}")
        return r.stdout.strip()

    # -- lifecycle --
    def configure(self, ticker: str) -> None:
        # Write the daemon's config JSON directly rather than via N `setconfig`
        # subprocesses — each setconfig cold-spawns the bundled daemon binary
        # (~1-2s), so 6 coins x ~6 keys added tens of seconds to startup. The
        # daemon isn't running yet here, so a direct merge-write is safe and fast.
        d = self.daemons[ticker]
        os.makedirs(d.datadir, exist_ok=True)
        # Owner-only datadir: it holds the daemon's rpcpassword (config) and the
        # plaintext wallet. 0700 stops another local user from reading the
        # rpcpassword and driving payto/broadcast directly (bypassing the API token).
        try:
            os.chmod(d.datadir, 0o700)
        except OSError:
            pass
        cfg_path = os.path.join(d.datadir, "config")
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
            except (ValueError, OSError):
                cfg = {}
        # Adopt any previously-written RPC credentials so a daemon that SURVIVED a prior app
        # run (e.g. a Windows orphan still holding this coin's fixed RPC port) stays
        # authenticable: daemon_alive()/start() then ADOPT it (start() is idempotent) instead
        # of fighting it for the port — the exact race that wedged Windows reopen at "Opening
        # your wallet". First run has no config, so a fresh random password is generated.
        if isinstance(cfg.get("rpcpassword"), str) and cfg["rpcpassword"]:
            d.rpc_password = cfg["rpcpassword"]
        if isinstance(cfg.get("rpcuser"), str) and cfg["rpcuser"]:
            d.rpc_user = cfg["rpcuser"]
        cfg.update({
            "rpcuser": d.rpc_user, "rpcpassword": d.rpc_password,
            "rpcport": d.rpc_port, "rpchost": "127.0.0.1", "rpcsock": "tcp",
            "config_version": cfg.get("config_version", 3),
            # Lightning: full gossip routing. Electrum's hardcoded trampoline nodes are
            # Bitcoin-mainnet (wrong-chain for these coins), so trampoline can't route;
            # gossip lets the wallet learn this chain's LN graph from its own peers.
            "use_gossip": True,
        })
        # The ElectrumX servers now return a real dynamic fee estimate (and a 2 sat/vB floor when they
        # can't), so use an ETA fee policy: sends, sweeps and channel open/close take the server's
        # network feerate. A typed per-send feerate still overrides it, and `_build_payto` retries at
        # the fixed fallback if a server ever can't estimate. Stored as the nested fee_policy.<name>
        # dict Electrum expects; preserve an explicit per-coin override if present.
        fee_policy = cfg.get("fee_policy") if isinstance(cfg.get("fee_policy"), dict) else {}
        for name in ("default", "lnwatcher", "swaps"):
            fee_policy[name] = "eta:2"
        cfg["fee_policy"] = fee_policy
        # Lightning channel funding/commitment fees read network.fee_estimates (the ETA estimates), so
        # let the Network poll the servers for them (re-enabled now that the servers return estimates).
        # `_inject_ln_fees` still seeds a value on bring-up so LN has fees before the first poll lands.
        cfg["test_disable_automatic_fee_eta_update"] = False
        # Lightning channel funding cap, per coin. Electrum's legacy non-wumbo limit is only
        # 2^24-1 = 16.7M sat (~0.167 coin); the fork supports wumbo, so lift the cap to the coin's
        # OWN max supply — but Lightning tracks balances in uint64 millisatoshi (capacity*1000 must fit
        # 2^64 ~ 1.8e16 sat), so cap at min(max_supply, 1e16 sat). 1e16 is under the msat ceiling and is
        # exact as a JS Number in the renderer. Real channels are tiny; this just removes the absurdly
        # low default while keeping a small-supply coin from opening a channel bigger than its supply.
        try:
            supply_coins = float(self.coins.get(ticker, {}).get("max_supply_btc") or 0)
        except (TypeError, ValueError):
            supply_coins = 0
        cfg["lightning_max_funding_sat"] = (
            min(int(supply_coins * 1e8), 10**16) if supply_coins > 0 else 10**16)
        if d.server and d.server != DAEMON_DEFAULT:   # explicit server: pin the daemon to it
            cfg["server"] = d.server
            cfg["auto_connect"] = True
        else:                              # baked default (online) or offline: write no explicit
            cfg.pop("server", None)        # server, so the variant's NETWORK_SERVER default +
            cfg.pop("auto_connect", None)  # servers.json drive it online with NATIVE roaming/failover.
            # NOTE: do NOT pin a single 'best' server here. Setting an explicit server makes electrum
            # 4.7.2 STOP roaming even with auto_connect=True (it does not fail over off an explicit
            # server in this fork) — that pinned BBTC to a flaky per-coin ElectrumX and hung it. Native
            # auto (no explicit server) keeps the full servers.json list and roams across them.
        # SOCKS5 proxy (Tor/privacy). Electrum stores it as the cfgstr "mode:host:port" under 'proxy'
        # plus 'proxy_user'/'proxy_password' and the 'enable_proxy' flag (simple_config.py). The creds
        # are secrets but the config file is already 0600. When unset, clear all proxy keys.
        px = d.proxy
        if px and px.get("host") and px.get("port"):
            cfg["proxy"] = f"socks5:{px['host']}:{px['port']}"
            cfg["enable_proxy"] = True
            if px.get("user"):
                cfg["proxy_user"] = px["user"]
            else:
                cfg.pop("proxy_user", None)
            if px.get("password"):
                cfg["proxy_password"] = px["password"]
            else:
                cfg.pop("proxy_password", None)
        else:
            cfg["proxy"] = "none"
            cfg["enable_proxy"] = False
            cfg.pop("proxy_user", None)
            cfg.pop("proxy_password", None)
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.chmod(tmp, 0o600)   # rpcpassword lives here — owner-only
        os.replace(tmp, cfg_path)

    def is_provisioned(self, ticker: str) -> bool:
        d = self.daemons[ticker]
        return os.path.exists(os.path.join(d.datadir, "wallets", "default_wallet"))

    # ---- at-rest encryption: session keys + password-in-body daemon RPC ------------------
    def set_session_keys(self, wallet_pws: Dict[str, str], contacts_key: Optional[bytes]) -> None:
        """Install the seed-derived per-coin wallet passwords + contacts key for this unlocked
        session. Held in memory only; cleared by stop_all. Idempotent."""
        self._wallet_pws = dict(wallet_pws or {})
        self._contacts_key = contacts_key

    def clear_session_keys(self) -> None:
        self._wallet_pws = {}
        self._contacts_key = None

    def locked(self) -> bool:
        """True when no session keys are installed (the soft-lock state): daemons keep running and
        stay synced, but signing / reveal-seed / change-password need the password re-entered."""
        return not self._wallet_pws

    @property
    def contacts_key(self) -> Optional[bytes]:
        return self._contacts_key

    def _wallet_pw(self, ticker: str) -> Optional[str]:
        return self._wallet_pws.get(ticker)

    def _wallet_path(self, ticker: str) -> str:
        return os.path.join(self.daemons[ticker].datadir, "wallets", "default_wallet")

    def _wallet_is_encrypted(self, ticker: str) -> bool:
        """True iff the on-disk wallet file is BIE1-encrypted (reads the magic; no daemon needed)."""
        try:
            with open(self._wallet_path(ticker), "rb") as f:
                return base64.b64decode(f.read(8))[:4] == b"BIE1"
        except Exception:
            return False

    def _drpc(self, ticker: str, method: str, params: dict, timeout: int = 60):
        """Call a coin daemon's JSON-RPC directly (loopback + BasicAuth) so secrets such as the
        wallet password ride in the request BODY, never on a command line (no ps/log leak)."""
        d = self.daemons[ticker]
        body = json.dumps({"id": 0, "jsonrpc": "2.0", "method": method, "params": params}).encode()
        auth = base64.b64encode(f"{d.rpc_user}:{d.rpc_password}".encode()).decode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{d.rpc_port}/", data=body,
            headers={"Content-Type": "application/json", "Authorization": "Basic " + auth})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
        if isinstance(resp, dict) and resp.get("error"):
            err = resp["error"]
            if isinstance(err, dict):
                msg = err.get("message") or ""
                data = err.get("data")
                # The daemon reports non-UserFacing exceptions with the generic message
                # "internal error while executing RPC" and stashes the real cause in
                # data.exception (e.g. "NotEnoughFunds()"). Surface that so callers — and
                # _friendly_send_error / _tx_build_error — can see the actual reason.
                if isinstance(data, dict) and data.get("exception"):
                    msg = f"{msg}: {data['exception']}" if msg else str(data["exception"])
                raise RuntimeError(f"{method}: {msg}")
            raise RuntimeError(f"{method}: {err}")
        result = resp.get("result") if isinstance(resp, dict) else None
        return result

    @staticmethod
    def _shred(path: str) -> None:
        """Best-effort overwrite-then-delete of a plaintext backup file."""
        try:
            with open(path, "r+b") as f:
                n = os.fstat(f.fileno()).st_size
                f.write(b"\x00" * n); f.flush(); os.fsync(f.fileno())
        except OSError:
            pass
        try:
            os.remove(path)
        except OSError:
            pass

    def ensure_encrypted(self, ticker: str) -> None:
        """Idempotently encrypt this coin's (already-loaded) wallet file at rest. No-op without
        session keys or if already encrypted. Crash-safe: a 0600 ``.bak.plain`` is taken before
        the in-place encrypt and removed only after the encrypted file verifies; an interrupted
        run is finished/rolled back on the next call. The wallet holds FUNDS — never a half-state."""
        wpw = self._wallet_pw(ticker)
        if not wpw:
            return
        path = self._wallet_path(ticker)
        bak = path + ".bak.plain"
        if os.path.exists(bak):                       # recover from an interrupted prior run
            if self._wallet_is_encrypted(ticker):
                self._shred(bak)                      # encrypt had succeeded -> drop plaintext backup
            else:
                os.replace(bak, path)                 # encrypt didn't finish -> revert to good plaintext
        if self._wallet_is_encrypted(ticker):
            return
        try:                                          # back up the plaintext file (0600), then encrypt
            fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as dst, open(path, "rb") as src:
                dst.write(src.read()); dst.flush(); os.fsync(dst.fileno())
        except OSError:
            return                                    # can't back up -> don't risk the wallet
        try:
            self._drpc(ticker, "password",
                       {"password": None, "new_password": wpw, "encrypt_file": True}, timeout=60)
            if not self._wallet_is_encrypted(ticker):
                raise RuntimeError("wallet not encrypted after password op")
            self._drpc(ticker, "load_wallet", {"password": wpw}, timeout=30)   # verify it reloads
            self._shred(bak)
        except Exception:
            if os.path.exists(bak):                   # roll back to the verified-good plaintext
                os.replace(bak, path)
            raise

    def provision(self, ticker: str, mnemonic: str, passphrase: str = "", *,
                  online: bool = False) -> None:
        """Restore the coin's wallet from the account zprv. The zprv is fed via STDIN using the
        `?` prompt — never argv. With session keys set + the daemon online, the restore writes an
        ENCRYPTED wallet (zprv + password in the RPC body). Use ``online=True`` when the daemon is
        already running; ``online=False`` (default) creates the wallet file before the daemon starts."""
        if self.is_provisioned(ticker):
            return
        d = self.daemons[ticker]
        zprv = provisioning.derive_account_xprv(
            mnemonic, passphrase, ticker=ticker, coins=self.coins)
        wpw = self._wallet_pw(ticker)
        if wpw and online:
            self._drpc(ticker, "restore",
                       {"text": zprv, "password": wpw, "encrypt_file": True}, timeout=90)
        else:
            self._checked(d, "restore", "?", online=online, stdin=zprv + "\n", timeout=90)

    @contextlib.contextmanager
    def _start_guard(self, d: CoinDaemon):
        """Exclusive lock around (re)starting ONE coin's daemon so a supervisor
        restart can't race a manual bring-up into duplicate processes (the stale-lock
        clear + Popen below is otherwise a TOCTOU window). No-op where fcntl is
        unavailable (Windows), where bring-up is single-threaded anyway."""
        if fcntl is None:
            yield
            return
        os.makedirs(d.datadir, exist_ok=True)
        with open(os.path.join(d.datadir, "orchestrator.start.lock"), "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _reap_foreign_daemons(self, d: CoinDaemon) -> None:
        """Kill any leftover daemon for THIS coin that belongs to a DIFFERENT app
        instance. Electrum's ``daemon -d`` double-forks, so on an unclean exit (SIGKILL,
        crash) or an AppImage that was replaced/upgraded, the daemon survives — reparented
        to init. Because every coin's RPC port is fixed (DEFAULT_RPC_PORTS), such an orphan
        squats the port and our own daemon can never bind it, so the coin hangs at
        "starting" forever (the stale-lockfile clear below doesn't help — a foreign process
        IS answering, just with a different rpcpassword). Match by the coin's daemon binary
        NAME but a DIFFERENT executable path (a separate or now-deleted mount), so we never
        touch the daemon we manage this session. Linux /proc only; best-effort, silent."""
        if not os.path.isdir("/proc"):   # non-Linux (Windows/macOS): no /proc; scan the RPC port
            self._reap_port_squatter(d)
            return
        if not d.bundled:
            # Source mode runs ``python <coin>/run_electrum daemon -d``. If the backend restarts,
            # the detached daemon may survive with the previous per-launch rpcpassword. A new
            # orchestrator cannot authenticate to it, so kill the exact coin workspace daemon and
            # let start() relaunch it with the freshly-written config.
            run_electrum = os.path.realpath(d.cmd[-1])
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/cmdline", "rb") as f:
                        argv = [a for a in f.read().split(b"\x00") if a]
                except OSError:
                    continue
                if b"daemon" not in argv:
                    continue
                paths = []
                for arg in argv:
                    try:
                        text = arg.decode("utf-8", "replace")
                    except Exception:
                        continue
                    if text.startswith("/"):
                        paths.append(os.path.realpath(text))
                if run_electrum not in paths:
                    continue
                pid = int(entry)
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    continue
                for _ in range(15):
                    time.sleep(0.2)
                    if not os.path.exists(f"/proc/{entry}"):
                        break
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
            return
        name = os.path.basename(d.cmd[0])   # e.g. "electrum-umo" — unique per coin
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    argv = f.read().split(b"\x00")
            except OSError:                 # process vanished or unreadable
                continue
            if not argv or not argv[0]:
                continue
            cmd0 = argv[0].decode("utf-8", "replace")
            if os.path.basename(cmd0) != name:   # not this coin's daemon binary
                continue
            if cmd0 == d.cmd[0]:                 # OUR daemon this session — leave it alone
                continue
            if b"daemon" not in argv:            # not a `daemon -d` invocation
                continue
            pid = int(entry)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
            for _ in range(15):                  # up to ~3s to flush + exit cleanly
                time.sleep(0.2)
                if not os.path.exists(f"/proc/{entry}"):
                    break
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def _listening_pids(self, port: int) -> list:
        """PIDs LISTENING on 127.0.0.1:<port>, without a psutil dependency. Windows: netstat -ano;
        macOS/BSD: lsof. Fast (~100ms) — used to gate the expensive RPC probes below so a clean
        start (free port) does no cold-spawn CLI round-trips. Best-effort; returns [] on any error."""
        try:
            if sys.platform == "win32":
                out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                     capture_output=True, text=True, timeout=10).stdout
                needle = ":" + str(port)
                pids = []
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[3].upper() == "LISTENING" \
                            and parts[1].endswith(needle) and parts[-1].isdigit():
                        pids.append(int(parts[-1]))
                return pids
            out = subprocess.run(["lsof", "-nP", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN", "-t"],
                                 capture_output=True, text=True, timeout=10).stdout
            return [int(x) for x in out.split() if x.isdigit()]
        except Exception:
            return []

    def _reap_port_squatter(self, d: CoinDaemon) -> None:
        """Non-/proc platforms (Windows AND macOS) counterpart of the /proc reaper. On a prior run
        that was not tree-killed, this coin's daemon can survive and squat its fixed RPC port. If
        the port holder does NOT accept our CURRENT rpcpassword — a foreign/old-password orphan,
        not an adoptable one (the persisted rpcpassword normally makes it adoptable) — kill the
        EXACT PID owning the port so our fresh daemon can bind. Kill by PID, NEVER by image name:
        in dev/source mode d.cmd[0] is ``python``, so an /IM kill would take out every Python
        process on the box. Runs before we spawn ours, so no daemon of ours is at risk; a holder
        that DOES accept our creds is adoptable and left alone. Best-effort, silent."""
        pids = self._listening_pids(d.rpc_port)
        if not pids:
            return   # free port (clean start) -> nothing to reap, no RPC probe needed
        if self.daemon_accepts_current_rpc(d.ticker, timeout=2.0):
            return   # adoptable survivor (same persisted rpcpassword) -> start() will adopt it
        me = os.getpid()
        for pid in pids:
            if pid == me:
                continue
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                   capture_output=True, timeout=10)
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    def _ensure_win_job(self):
        """Create (once) a Windows Job Object with KILL_ON_JOB_CLOSE. Every daemon Popen is put in
        it, so when THIS supervisor exits for ANY reason (clean quit, crash, taskkill) the OS
        terminates the whole daemon tree — orphaned daemons become impossible on Windows, which is
        the root cause of the reopen hang. No-op/None off Windows or on any failure (persist+reaper
        remain the cross-platform fallback)."""
        if sys.platform != "win32":
            return None
        if self._win_job is not None:
            return self._win_job
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ULONG_PTR = ctypes.c_size_t
            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ULONG_PTR),
                            ("MaximumWorkingSetSize", ULONG_PTR),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ULONG_PTR),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]
            class IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
            class EXT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC),
                            ("ProcessMemoryLimit", ULONG_PTR), ("JobMemoryLimit", ULONG_PTR),
                            ("PeakProcessMemoryUsed", ULONG_PTR), ("PeakJobMemoryUsed", ULONG_PTR)]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                         wintypes.LPVOID, wintypes.DWORD]
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = EXT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9,  # JobObjectExtendedLimitInformation
                                                    ctypes.byref(info), ctypes.sizeof(info)):
                return None
            self._win_kernel32 = kernel32
            self._win_job = job
            return job
        except Exception:
            return None

    def _assign_to_win_job(self, proc) -> None:
        """Put a freshly-spawned daemon into the KILL_ON_JOB_CLOSE job (Windows only, best-effort).
        Nested jobs (Win8+) let this coexist with any parent job Electron may impose."""
        if sys.platform != "win32" or proc is None:
            return
        job = self._ensure_win_job()
        if not job:
            return
        try:
            self._win_kernel32.AssignProcessToJobObject(job, int(proc._handle))
        except Exception:
            pass

    def start(self, ticker: str) -> None:
        d = self.daemons[ticker]
        with self._start_guard(d):
            if self._stopping:   # a supervisor tick that raced stop_all into the lock: veto
                return
            if self.daemon_alive(ticker):   # already running (start_coin vs supervisor race): idempotent
                return
            # Reap any daemon for THIS coin left behind by a PREVIOUS app instance (a
            # replaced or crashed AppImage orphans its double-forked `daemon -d` to init,
            # where it squats this coin's fixed RPC port and blocks our own daemon from
            # binding it). Do this BEFORE the lockfile clear + Popen below.
            self._reap_foreign_daemons(d)
            # An orphan the reaper deliberately kept alive because it answers our CURRENT
            # rpcpassword (an adoptable survivor of a prior run) must be ADOPTED, not duplicated:
            # spawning a second daemon here would only fail to bind the squatted port and leave a
            # dead process behind. Re-check now the reaper has settled — cross-platform (this is
            # what makes a persisted-rpcpassword adopt deterministic on Win/macOS, which have no
            # foreign-daemon /proc reaper, and tightens Linux too). Gate the cold-spawn liveness
            # probe on a fast port check so a clean start (free port) never pays for it.
            if self._listening_pids(d.rpc_port) and self.daemon_alive(ticker):
                return
            # A crash or SIGKILL leaves a stale `daemon` lockfile that makes the next
            # daemon startup refuse to start ("Daemon already running (lockfile detected)").
            # If nothing is actually answering RPC, clear it so the daemon can relaunch.
            lock = os.path.join(d.datadir, "daemon")
            if os.path.exists(lock) and not self.daemon_alive(ticker):
                try:
                    os.remove(lock)
                except OSError:
                    pass
            # A coin runs OFFLINE only when explicitly set offline (server is None); the
            # daemon still comes up and loads its wallet (address + cached balance). By
            # default every coin is online — server is the DAEMON_DEFAULT sentinel or an
            # explicit host, both truthy, so only an explicit None gets --offline.
            cmd = list(d.cmd)
            if not d.server:
                cmd.append("--offline")
            cmd.append("daemon")
            if sys.platform != "win32":
                cmd.append("-d")
            d.proc = subprocess.Popen(cmd, cwd=d.cwd, env=self._env(d),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._assign_to_win_job(d.proc)   # Windows: OS-guaranteed cleanup when this supervisor dies

    def wait_ready(self, ticker: str, timeout: float = 45.0) -> bool:
        d = self.daemons[ticker]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Readiness = the daemon's RPC is responsive, NOT that it's connected to a
            # server. `list_wallets` answers on a running daemon whether it's online or
            # offline, and even before a wallet exists (returns "[]"), so a serverless
            # coin still becomes ready.
            r = self._run(d, "list_wallets", timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return True
            time.sleep(0.5)
        return False

    def _load_ln_hubs(self) -> Dict[str, str]:
        """Per-coin LN hub connection strings (node_id@host:port). Sidecar wins; falls back to a
        baked coins.json ``ln_hub`` per coin. Empty/invalid entries are dropped."""
        hubs: Dict[str, str] = {}
        for t in self.daemons:
            baked = (self.coins.get(t, {}) or {}).get("ln_hub")
            if isinstance(baked, str) and baked.strip():
                hubs[t] = baked.strip()
        try:
            with open(self._ln_hubs_path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for t, v in saved.items():
                    if isinstance(v, str) and v.strip():
                        hubs[t.upper()] = v.strip()
        except (OSError, ValueError):
            pass
        return hubs

    def ln_hub(self, ticker: str) -> Optional[str]:
        return self._ln_hubs.get(ticker)

    def _connect_ln_hub(self, ticker: str) -> None:
        """Best-effort: enable Lightning and connect to this coin's configured hub, so the wallet is
        peered to a routable node out of the box. Idempotent + non-fatal (a down hub or a still-
        connecting daemon must never block bring-up)."""
        hub = self._ln_hubs.get(ticker)
        if not hub or not self.daemons[ticker].server:
            return
        try:
            self._drpc(ticker, "init_lightning", {}, timeout=20)   # idempotent
        except Exception:
            pass
        try:
            self._drpc(ticker, "add_peer", {"connection_string": hub}, timeout=30)
            self._ln_hub_connected.add(ticker)
        except Exception:
            pass

    def _inject_ln_fees(self, ticker: str) -> None:
        """Seed the daemon's network fee estimates. Lightning channel funding + commitment fees read
        ``network.fee_estimates`` (the ETA estimates), NOT the FEE_POLICY config — and these chains'
        servers return no estimate, so every LN op fails with NoDynamicFeeEstimates. Inject a fixed
        ~2 sat/vB (2000 sat/kvB) at each ETA target so channels can open/close and sends estimate.
        configure() disables the network's auto-update so this persists. Best-effort + idempotent;
        re-run on every (re)start. No-op for an offline coin (no network to seed)."""
        if not self.daemons[ticker].server:
            return
        try:
            # fee_est as a STRING so the daemon literal_evals it to INT keys (eta_target_to_fee reads
            # int targets; a JSON dict would arrive with string keys and never match).
            est = "{1: 2000, 2: 2000, 5: 2000, 10: 2000, 25: 2000, 144: 2000, 1008: 2000}"
            self._drpc(ticker, "test_inject_fee_etas", {"fee_est": est}, timeout=20)
        except Exception:
            pass

    def load(self, ticker: str) -> None:
        wpw = self._wallet_pw(ticker)
        if wpw:
            # Password in the BODY. Required for an encrypted wallet; ignored by the daemon for a
            # still-plaintext one (pre-migration) — so this is correct for both states.
            self._drpc(ticker, "load_wallet", {"password": wpw}, timeout=30)
        else:
            self._checked(self.daemons[ticker], "load_wallet", timeout=30)
        self._loaded.add(ticker)

    def bring_up(self, ticker: str, mnemonic: Optional[str] = None,
                 passphrase: str = "", ready_timeout: float = 45.0) -> None:
        """configure -> (provision) -> start daemon -> wait ready -> load_wallet."""
        self.configure(ticker)
        if mnemonic is not None:
            self.provision(ticker, mnemonic, passphrase)
        self.start(ticker)
        if not self.wait_ready(ticker, timeout=ready_timeout):
            raise RuntimeError(f"{ticker} daemon did not become ready in {ready_timeout}s")
        self._inject_ln_fees(ticker)   # seed network fee estimates so Lightning (and sends) work
        # Only load when we just provisioned from a seed. On a relaunch (no seed),
        # leave the wallet unloaded so the UI requires an explicit unlock.
        if mnemonic is not None and self.is_provisioned(ticker):
            self.load(ticker)
            self._verify_seed_match(ticker, mnemonic, passphrase)
            try:
                self.ensure_encrypted(ticker)   # encrypt-at-rest (idempotent, crash-safe)
            except Exception:
                pass
            self._connect_ln_hub(ticker)        # peer to this coin's LN hub (best-effort)

    def bring_up_all(self, mnemonic: Optional[str] = None, passphrase: str = "") -> dict:
        """Best-effort bring-up of every coin. One coin failing (e.g. a transient
        daemon hiccup) must NOT abort the others or crash the backend — collect the
        failures and keep serving so the multiwallet still loads. Coins come up in
        PARALLEL (each is independent — own datadir, RPC port, daemon), so startup is
        ~one daemon's time rather than six in series. Returns ``{ticker: error}`` for
        any that didn't come up."""
        errors = {}
        # When bringing up FROM a seed (CLI --create/--restore smoke path), install the at-rest
        # encryption keys so freshly-restored wallets are written encrypted, matching the unlock
        # path. The normal app start passes mnemonic=None (no provision) and sets keys at unlock.
        if mnemonic is not None:
            try:
                wallet_pws, contacts_key = vault.derive_session_keys(mnemonic, list(self.daemons))
                self.set_session_keys(wallet_pws, contacts_key)
            except Exception:
                pass
        # Only start the coins the user chose to auto-start; the rest stay "stopped" (known,
        # listed, startable on demand). ``self._active`` gates the supervisor's auto-restart.
        active = set(self.active_tickers())
        self._active = set(active)
        for t in self.daemons:
            self.status[t] = "starting" if t in active else "stopped"
        if active:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(active)) as ex:
                # 60s (not the default 45) of cold-start margin: ~200MB PyInstaller daemons
                # spawning at once can make a couple slow to answer; the supervisor still heals any
                # that come up after this anyway, but the extra margin avoids the dim-then-light flicker.
                futures = {ex.submit(self.bring_up, t, mnemonic, passphrase, ready_timeout=60.0): t
                           for t in active}
                for fut in concurrent.futures.as_completed(futures):
                    ticker = futures[fut]
                    try:
                        fut.result()
                        self.status[ticker] = "ready"
                    except Exception as e:
                        self.status[ticker] = "failed"
                        errors[ticker] = str(e)[:200]
        # Seed path (CLI --create/--restore): also write the wallet FILE for the coins we did NOT
        # start, so a later start_coin is password-free (mirrors provision_all's offline-provision
        # at unlock in the packaged app, where bring_up_all runs with mnemonic=None).
        if mnemonic is not None:
            for t in self.daemons:
                if t not in active:
                    try:
                        self.provision(t, mnemonic, passphrase, online=False)
                    except Exception:
                        pass
        # Initial bring-up is done: the supervisor may now keep crashed daemons alive
        # without racing the startup it would otherwise have collided with.
        self._supervision_enabled = True
        self.schedule_dex_announce("bring-up")
        return errors

    # -- queries / aggregation --
    def rpc(self, ticker: str, command: str, *args, timeout: int = 60, stdin: Optional[str] = None):
        out = self._checked(self.daemons[ticker], command, *args, timeout=timeout, stdin=stdin)
        try:
            result = json.loads(out)
        except (ValueError, json.JSONDecodeError):
            return out
        return result

    def first_address(self, ticker: str) -> Optional[str]:
        addrs = self.rpc(ticker, "listaddresses")
        return addrs[0] if isinstance(addrs, list) and addrs else None

    def addresses(self, ticker: str, *, kind: str = "receiving", limit: int = 1000) -> list:
        """Wallet address list for the Addresses tab: address + balance + label + used + change.
        listaddresses returns labels repr()-wrapped and has no per-row 'used' flag, so we
        diff against the --unused set; the --change set marks each row as change vs receiving.
        ``kind`` filters the result: 'receiving' (default) drops change addresses, 'change'
        keeps ONLY change addresses, 'all' keeps every address. Returns [] if the wallet is
        not ready (like history)."""
        import ast
        try:
            rows = self.rpc(ticker, "listaddresses", "--balance", "--labels")
            unused = set(self.rpc(ticker, "listaddresses", "--unused") or [])
            change_set = set(self.rpc(ticker, "listaddresses", "--change") or [])
        except Exception:
            return []
        out = []
        for item in (rows or []):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            addr, bal, label_repr = item[0], item[1], item[2]
            is_change = addr in change_set
            if kind == "receiving" and is_change:
                continue
            if kind == "change" and not is_change:
                continue
            try:
                label = ast.literal_eval(label_repr) if isinstance(label_repr, str) else ""
            except (ValueError, SyntaxError):
                label = str(label_repr)
            out.append({"address": addr, "balance": str(bal),
                        "label": label if isinstance(label, str) else "",
                        "used": addr not in unused,
                        "change": is_change})
            if len(out) >= limit:
                break
        return out

    def validate_address(self, ticker: str, address: str) -> bool:
        """True iff `address` is valid for THIS coin (evaluated under the daemon's own
        constants.net, so cross-coin addresses are rejected). Used by the contacts store."""
        if not isinstance(address, str) or address.startswith("-"):
            return False
        try:
            return bool(self.rpc(ticker, "validateaddress", address))
        except Exception:
            return False

    def set_label(self, ticker: str, key: str, label: str) -> bool:
        """Set (or clear, when label is empty) the Electrum label for an address or txid."""
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("missing key")
        k = key.strip()
        if k.startswith("-"):                      # never let a key/label be parsed as a CLI flag
            raise RuntimeError("invalid key")
        lbl = (label or "")
        try:
            self.rpc(ticker, "setlabel", "--", k, lbl)   # '--' stops option parsing so a label like "-x" is safe
        except Exception:
            # some electrum CLIs don't accept '--' for setlabel; retry positionally
            self.rpc(ticker, "setlabel", k, lbl)
        return True

    # -- tools: load transaction --
    def _normalize_tx(self, raw: str, j: dict) -> dict:
        """Shape a daemon ``deserialize`` result into the viewer's response: version, locktime,
        inputs/outputs (each output carrying its address + value), the summed output total, and a
        locally computed txid + size (electrum is importable in the supervisor; raw-tx inputs carry
        no value, so a fee is only derivable when input values are present — left to the daemon)."""
        outputs = j.get("outputs") or []
        total_out = 0
        for o in outputs:
            if isinstance(o, dict):
                try:
                    total_out += int(o.get("value_sats") or 0)
                except (TypeError, ValueError):
                    pass
        txid = size = complete = None
        try:
            from electrum.transaction import tx_from_any
            tx = tx_from_any(raw)
            try:
                txid = tx.txid()
            except Exception:
                txid = None
            try:
                size = tx.estimated_size()
            except Exception:
                size = None
            try:
                complete = tx.is_complete()
            except Exception:
                complete = None
        except Exception:
            pass
        return {
            "raw": raw,
            "version": j.get("version"),
            "locktime": j.get("locktime"),
            "inputs": j.get("inputs") or [],
            "outputs": outputs,
            "total_out_sats": total_out,
            "txid": txid,
            "size": size,
            "complete": complete,
        }

    def load_transaction(self, ticker: str, raw: str) -> dict:
        """Deserialize a raw transaction (hex or PSBT base64) for the Tools 'Load transaction'
        viewer. Offline: the daemon's ``deserialize`` is a pure function (no wallet/network)."""
        raw = (raw or "").strip()
        if not raw:
            raise RuntimeError("no transaction provided")
        if len(raw) > 200_000:        # belt-and-suspenders over the API's 64KB body cap
            raise RuntimeError("transaction too large")
        j = self._drpc(ticker, "deserialize", {"tx": raw}, timeout=30)
        if not isinstance(j, dict):
            raise RuntimeError("could not deserialize transaction")
        return self._normalize_tx(raw, j)

    def fetch_transaction(self, ticker: str, txid: str) -> dict:
        """Fetch a raw transaction from the network by txid (online), then deserialize it for the
        viewer. Raises if the coin's daemon is offline or the txid is unknown to the server."""
        txid = (txid or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", txid):
            raise RuntimeError("invalid txid")
        try:
            raw = self._drpc(ticker, "gettransaction", {"txid": txid}, timeout=60)
        except Exception:
            # the daemon raises "Unknown transaction" when the server doesn't have it
            raise RuntimeError(f"transaction not known to the {ticker} server (still syncing, or wrong coin)")
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"transaction not known to the {ticker} server (still syncing, or wrong coin)")
        return self.load_transaction(ticker, raw)

    def broadcast_transaction(self, ticker: str, raw: str) -> str:
        """Broadcast a signed raw transaction to the network; returns the txid. Online only."""
        raw = (raw or "").strip()
        if not raw:
            raise RuntimeError("no transaction provided")
        txid = self._drpc(ticker, "broadcast", {"tx": raw}, timeout=60)
        if not isinstance(txid, str):
            raise RuntimeError("broadcast failed")
        return txid

    # -- tools: sign / verify message --
    def sign_message(self, ticker: str, address: str, message: str) -> str:
        """Sign ``message`` with the private key of ``address`` (which must be one of THIS wallet's
        addresses). The wallet password rides in the request body (``_drpc``), never on argv."""
        address = (address or "").strip()
        if not address:
            raise RuntimeError("address is required")
        sig = self._drpc(ticker, "signmessage",
                         {"address": address, "message": message or "",
                          "password": self._wallet_pw(ticker)}, timeout=30)
        if not isinstance(sig, str):
            raise RuntimeError("could not sign message")
        return sig

    def verify_message(self, ticker: str, address: str, signature: str, message: str) -> bool:
        """Verify that ``signature`` over ``message`` was produced by ``address`` (any address, not
        only this wallet's). Offline — no wallet, no password."""
        address = (address or "").strip()
        signature = (signature or "").strip()
        if not address or not signature:
            raise RuntimeError("address and signature are required")
        return bool(self._drpc(ticker, "verifymessage",
                    {"address": address, "signature": signature, "message": message or ""}, timeout=30))

    # -- tools: coin control --
    def list_utxos(self, ticker: str) -> list:
        """Wallet UTXOs for the coin-control table: address, amount (coin units), outpoint
        (txid:vout), confirmations height, coinbase flag, and a ``frozen`` flag (true when the
        UTXO's address is frozen — frozen coins are excluded from spends). Returns [] when the
        wallet isn't ready, like ``addresses``/``history``."""
        try:
            utxos = self.rpc(ticker, "listunspent")
            frozen = set(self.rpc(ticker, "listaddresses", "--frozen") or [])
            tip = self._chain_tip_height(ticker)
        except Exception:
            return []
        out = []
        for u in (utxos or []):
            if not isinstance(u, dict):
                continue
            addr = u.get("address")
            out.append({
                "address": addr,
                "amount": u.get("value"),            # already a coin-unit string (format_satoshis)
                "txid": u.get("prevout_hash"),
                "vout": u.get("prevout_n"),
                "height": u.get("height"),
                "confirmations": self._utxo_confirmations(u, tip),
                "coinbase": bool(u.get("coinbase")),
                "frozen": addr in frozen,
            })
        return out

    def set_address_frozen(self, ticker: str, address: str, frozen: bool) -> bool:
        """Freeze (or unfreeze) one of the wallet's addresses. A frozen address's coins are kept out
        of every spend until unfrozen. Freezing an address freezes all UTXOs sitting on it."""
        address = (address or "").strip()
        if not address:
            raise RuntimeError("address is required")
        cmd = "freeze" if frozen else "unfreeze"
        return bool(self._drpc(ticker, cmd, {"address": address}, timeout=30))

    # -- tools: encrypt / decrypt message --
    def _resolve_pubkey(self, ticker: str, key: str) -> str:
        """Accept either a hex public key (66/130 chars) or one of the wallet's addresses, and
        return the public key. Lets the user think in addresses while the ECIES commands take a
        pubkey."""
        key = (key or "").strip()
        if not key:
            raise RuntimeError("public key or address is required")
        if re.fullmatch(r"[0-9a-fA-F]{66}|[0-9a-fA-F]{130}", key):
            return key
        keys = self._drpc(ticker, "getpubkeys", {"address": key}, timeout=30)
        if isinstance(keys, (list, tuple)) and keys:
            return keys[0]
        if isinstance(keys, str) and keys:
            return keys
        raise RuntimeError("no public key for that address (is it one of your addresses?)")

    def encrypt_message(self, ticker: str, key: str, message: str) -> str:
        """ECIES-encrypt ``message`` to ``key`` (a recipient public key, or one of your addresses).
        Offline — encryption needs only the public key."""
        pubkey = self._resolve_pubkey(ticker, key)
        enc = self._drpc(ticker, "encrypt", {"pubkey": pubkey, "message": message or ""}, timeout=30)
        if not isinstance(enc, str):
            raise RuntimeError("could not encrypt message")
        return enc

    def decrypt_message(self, ticker: str, key: str, encrypted: str) -> str:
        """Decrypt an ECIES message addressed to one of THIS wallet's keys. ``key`` is the matching
        public key or wallet address; the private key + password ride in the request body."""
        encrypted = (encrypted or "").strip()
        if not encrypted:
            raise RuntimeError("encrypted message is required")
        pubkey = self._resolve_pubkey(ticker, key)
        dec = self._drpc(ticker, "decrypt",
                        {"pubkey": pubkey, "encrypted": encrypted,
                         "password": self._wallet_pw(ticker)}, timeout=30)
        if not isinstance(dec, str):
            raise RuntimeError("could not decrypt message")
        return dec

    # -- tools: advanced transactions (build now, broadcast after review) --
    @staticmethod
    def _tx_build_error(msg: str) -> str:
        """User-facing message for a build/sweep/bump failure: map known causes (insufficient funds,
        bad address, dust, no connection) via the shared mapper, otherwise keep the daemon's own
        (already short, UserFacing) message — e.g. 'Transaction not in wallet.' — with the
        ``<method>: `` prefix and internal-error noise stripped."""
        msg = str(msg or "")
        low = msg.lower()
        if any(s in low for s in ("notenoughfunds", "not enough funds", "insufficient", "dust",
                                  "not connected", "no server", "timeout", "timed out",
                                  "checksum", "not a valid", "invalid address", "could not connect")):
            return _friendly_send_error(msg)
        cleaned = msg.split(": ", 1)[-1] if ":" in msg else msg
        cleaned = cleaned.replace("internal error while executing RPC", "").strip(" :")
        return cleaned or "The transaction could not be built."

    def pay_to_many(self, ticker: str, outputs: list, feerate: Optional[str] = None,
                    from_coins=None) -> dict:
        """Build + sign a multi-output transaction. ``outputs`` = ``[[address, amount], ...]`` with
        amounts in coin units (``!`` = send max on a single output). Does NOT broadcast — returns the
        normalized tx for review; the user broadcasts it from the viewer. ``feerate`` (sat/vByte) is
        optional; omit to use the coin's configured fee policy. ``from_coins`` (optional list of
        ``"txid:vout"``) restricts the inputs to the coins selected in Send's coin control."""
        from_coins = self._confirmed_send_coins(ticker, self._validate_from_coins(from_coins))
        if not isinstance(outputs, list) or not outputs:
            raise RuntimeError("at least one output is required")
        norm = []
        for o in outputs:
            if not isinstance(o, (list, tuple)) or len(o) != 2:
                raise RuntimeError("each output must be [address, amount]")
            addr, amt = str(o[0]).strip(), str(o[1]).strip()
            if not addr or not amt:
                raise RuntimeError("address and amount are required for each output")
            if addr.startswith("-"):                       # consistency with the /send guard
                raise RuntimeError("invalid address")
            if amt != "!":                                 # '!' = send max; otherwise a positive number
                try:
                    if float(amt) <= 0:
                        raise RuntimeError("amount must be greater than zero")
                except (TypeError, ValueError):
                    raise RuntimeError(f"invalid amount: {amt}")
            norm.append([addr, amt])
        params = {"outputs": norm, "password": self._wallet_pw(ticker)}
        if feerate:
            params["feerate"] = str(feerate)
        # Electrum's paytomany does from_coins.split(',') — pass the comma-joined string.
        params["from_coins"] = ",".join(from_coins)
        raw = self._drpc_fee_fallback(ticker, "paytomany", params, timeout=90)
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("could not build transaction")
        return self.load_transaction(ticker, raw)

    def sweep_to(self, ticker: str, privkey: str, destination: str, feerate: Optional[str] = None) -> dict:
        """Build a transaction sweeping all funds controlled by ``privkey`` to ``destination``. The
        private key rides in the request BODY (never argv). Online (queries the key's UTXOs). Returns
        the normalized tx for review; the user broadcasts it."""
        privkey = (privkey or "").strip()
        destination = (destination or "").strip()
        if not privkey or not destination:
            raise RuntimeError("private key and destination are required")
        params = {"privkey": privkey, "destination": destination}
        if feerate:
            params["feerate"] = str(feerate)
        raw = self._drpc_fee_fallback(ticker, "sweep", params, timeout=120)
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("nothing to sweep (no funds on that key, or wrong network?)")
        return self.load_transaction(ticker, raw)

    def bump_fee(self, ticker: str, tx: str, new_feerate: str) -> dict:
        """Build a higher-fee Replace-By-Fee replacement of an UNCONFIRMED wallet transaction. ``tx``
        is a txid already in the wallet's history, or a raw hex tx. Online. Returns the normalized
        replacement for review. Whether the network accepts the replacement depends on the coin's
        mempool policy (and the original tx having signalled RBF) — broadcast surfaces that."""
        tx = (tx or "").strip()
        if not tx:
            raise RuntimeError("transaction id or hex is required")
        if not new_feerate:
            raise RuntimeError("a new fee rate is required")
        try:
            raw = self._drpc(ticker, "bumpfee",
                            {"tx": tx, "new_fee_rate": str(new_feerate),
                             "password": self._wallet_pw(ticker)}, timeout=90)
        except Exception as e:
            raise RuntimeError(self._tx_build_error(str(e)))
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("could not bump fee")
        return self.load_transaction(ticker, raw)

    # -- tools: keys & seed (SENSITIVE — reveal routes re-verify the password in api.py) --
    def master_pubkey(self, ticker: str) -> str:
        """Master public key (xpub) for the coin's wallet. PUBLIC — safe to share; used for
        watch-only imports. No password needed."""
        mpk = self.rpc(ticker, "getmpk")
        if isinstance(mpk, str):
            return mpk
        raise RuntimeError("could not read master public key")

    def wallet_info(self, ticker: str) -> dict:
        """Read-only wallet identity for the Settings → Wallet information panel: the account master
        public key (xpub), its BIP84 derivation path, the script type, and a best-effort key fingerprint.
        PUBLIC / watch-only — no seed and no password needed."""
        coin_type = int(self.coins.get(ticker, {}).get("coin_type") or 0)
        mpk = self.master_pubkey(ticker)
        # Best-effort fingerprint of the account xpub (HASH160(pubkey)[:4]); never touches the seed.
        fp = None
        try:
            from electrum import bip32
            fp = bip32.BIP32Node.from_xkey(mpk).calc_fingerprint_of_this_node().hex()
        except Exception:
            fp = None
        return {
            "mpk": mpk,
            "coin_type": coin_type,
            "derivation_path": provisioning.account_derivation_path(coin_type, 0),
            "script_type": "p2wpkh",
            "fingerprint": fp,
        }

    def export_privkey(self, ticker: str, address: str) -> str:
        """WIF private key for one of the wallet's addresses. SENSITIVE: the API route MUST have
        verified the user's re-prompted password against the vault before calling this. The wallet
        password rides in the request BODY (never argv)."""
        address = (address or "").strip()
        if not address:
            raise RuntimeError("address is required")
        keys = self._drpc(ticker, "getprivatekeys",
                          {"address": address, "password": self._wallet_pw(ticker)}, timeout=30)
        if isinstance(keys, list):
            if not keys:
                raise RuntimeError("no private key for that address")
            return keys[0]
        if isinstance(keys, str) and keys:
            return keys
        raise RuntimeError("no private key for that address (is it one of your addresses?)")

    # NOTE: importprivkey is intentionally NOT exposed — this is a seed-derived HD wallet, and
    # Electrum refuses private-key import on HD wallets ("this type of wallet cannot import private
    # keys"). Sweeping an external key to a wallet address (Advanced transactions -> Sweep) is the
    # supported way to bring outside funds in.

    # -- lightning --
    def enable_lightning(self, ticker: str) -> dict:
        """Turn on Lightning for this coin's wallet (idempotent — the fork's init_lightning
        catches the already-enabled case). init_lightning is password-gated (@command('wp')), so the
        password flag is checked BEFORE the already-enabled short-circuit — a password-less call
        therefore fails with 'Password required' on an encrypted wallet. Pass the wallet password in
        the request BODY (never argv), like the other signing LN calls; it's None for an unencrypted
        wallet, which the daemon accepts."""
        return self._drpc(ticker, "init_lightning", {"password": self._wallet_pw(ticker)})

    @staticmethod
    def _ln_guard(*values):
        # User-supplied LN values (connection strings, invoices, channel points, amounts)
        # must not begin with '-' or they could be parsed as CLI options to the daemon.
        for v in values:
            if isinstance(v, str) and v.startswith("-"):
                raise RuntimeError("invalid lightning argument")

    def ln_list_channels(self, ticker: str):
        return self.rpc(ticker, "list_channels")

    def ln_nodeid(self, ticker: str):
        return self.rpc(ticker, "nodeid")

    def ln_history(self, ticker: str):
        h = self.rpc(ticker, "lightning_history")
        return h.get("transactions", h) if isinstance(h, dict) else h

    def merged_ln_history(self, limit: int = 200) -> list:
        """Cross-coin Lightning history, each entry tagged with its coin, newest first. Fetched on
        the main poll like the on-chain merged feed so LN history is cached + self-heals (a one-shot
        per-coin fetch that lands empty would otherwise never refill)."""
        merged = []
        for ticker in self.daemons:
            h = self.ln_history(ticker)
            if not isinstance(h, list):
                continue
            for tx in h:
                if not isinstance(tx, dict):
                    continue
                entry = dict(tx)
                entry["coin"] = ticker
                merged.append(entry)

        def _ts(e):
            try:
                return float(e.get("timestamp"))
            except (TypeError, ValueError):
                return float("-inf")
        merged.sort(key=_ts, reverse=True)
        return merged[:limit]

    def ln_decode(self, ticker: str, invoice: str):
        self._ln_guard(invoice)
        return self.rpc(ticker, "decode_invoice", invoice)

    def ln_open(self, ticker: str, connection_string: str, amount: str, push_amount: str = ""):
        # Opening a channel SIGNS the funding tx -> the encrypted wallet needs the password (in the
        # body, never argv). connection_string/amount/push_amount also ride in the body, so a leading
        # '-' is safe. push_amount gives the peer starting balance, creating receive capacity.
        body = {"connection_string": connection_string, "amount": amount,
                "password": self._wallet_pw(ticker)}
        push = str(push_amount or "").strip()
        if push:
            body["push_amount"] = push
        try:
            result = self._drpc(ticker, "open_channel", body, timeout=150)
        except Exception as e:
            raise
        return result

    def ln_close(self, ticker: str, channel_point: str, force: bool = False):
        body = {"channel_point": channel_point, "force": bool(force),
                "password": self._wallet_pw(ticker)}
        try:
            result = self._drpc(ticker, "close_channel", body, timeout=150)
        except Exception as e:
            raise
        return result

    def ln_pay(self, ticker: str, invoice: str, *, timeout=None, max_cltv=None, max_fee_msat=None):
        body = {"invoice": invoice, "password": self._wallet_pw(ticker)}
        if timeout is not None:
            body["timeout"] = int(timeout)
        if max_cltv is not None:
            body["max_cltv"] = int(max_cltv)
        if max_fee_msat is not None:
            body["max_fee_msat"] = int(max_fee_msat)
        try:
            result = self._drpc(ticker, "lnpay", body, timeout=180)
        except Exception as e:
            raise
        return result

    def ln_invoice(self, ticker: str, amount: str, memo: str = "", expiry: str = "3600"):
        # memo passed as --memo=<value> so arbitrary text (even a leading '-') is safe.
        self._ln_guard(str(amount), str(expiry))
        try:
            result = self.rpc(ticker, "add_request", str(amount), "--lightning",
                              "--memo=" + str(memo), "--expiry", str(expiry), timeout=60)
        except Exception as e:
            raise
        return result

    def ln_status(self, ticker: str) -> dict:
        """LN dashboard summary: enabled?, open-channel count, backups, node id, and total
        sendable/receivable sats across OPEN channels (these chains are direct-channels-only)."""
        try:
            chans = self.rpc(ticker, "list_channels")
            clist = chans if isinstance(chans, list) else []
            opens = [c for c in clist if isinstance(c, dict) and c.get("type") == "CHANNEL"]
            try:
                capacity = self.rpc(ticker, "lightning_capacity")
            except Exception:
                capacity = None
            if isinstance(capacity, dict):
                send = int(capacity.get("can_send_sat") or 0)
                recv = int(capacity.get("can_receive_sat") or 0)
                num_channels = int(capacity.get("num_channels") or len(opens))
            else:
                send = sum(int(c.get("local_balance") or 0) for c in opens)
                recv = sum(int(c.get("remote_balance") or 0) for c in opens)
                num_channels = len(opens)
            try:
                node = self.rpc(ticker, "nodeid")
            except Exception:
                node = None
            hub = self._ln_hubs.get(ticker)
            # "hub_channel" = you have a channel WITH the hub (the useful signal; Electrum doesn't keep
            # a persistent bare peer connection, so peer-presence would be misleading).
            hub_channel = False
            if hub:
                hub_id = hub.split("@", 1)[0].lower()
                hub_channel = any(str(c.get("remote_pubkey", "")).lower() == hub_id for c in opens)
            return {"enabled": True, "num_channels": num_channels,
                    "num_backups": len(clist) - len(opens),
                    "node_id": node if isinstance(node, str) else None,
                    "can_send_sat": send, "can_receive_sat": recv,
                    "hub": hub, "hub_channel": hub_channel}
        except Exception:
            return {"enabled": False, "num_channels": 0, "num_backups": 0,
                    "node_id": None, "can_send_sat": 0, "can_receive_sat": 0,
                    "hub": self._ln_hubs.get(ticker), "hub_connected": False}

    # -- lightning: backups, peers, recovery, requests (all viable for DIRECT channels) --
    def ln_export_backup(self, ticker: str, channel_point: str):
        """Encrypted static channel backup for one channel — lets funds be recovered if wallet state
        is lost (there's no watchtower/LSP safety net on these chains). Needs the wallet password."""
        channel_point = (channel_point or "").strip()
        if not channel_point:
            raise RuntimeError("channel is required")
        return self._drpc(ticker, "export_channel_backup",
                          {"channel_point": channel_point, "password": self._wallet_pw(ticker)}, timeout=60)

    def ln_import_backup(self, ticker: str, encrypted: str):
        """Import an encrypted channel backup (recovery)."""
        encrypted = (encrypted or "").strip()
        if not encrypted:
            raise RuntimeError("backup is required")
        return self._drpc(ticker, "import_channel_backup", {"encrypted": encrypted}, timeout=60)

    def ln_request_force_close(self, ticker: str, channel_point: str, connection_string: str = ""):
        """Ask the remote peer to force-close a channel — recovers funds from a BACKUP (no local
        state needed) when given the peer's connection string. Needs the wallet password + network."""
        channel_point = (channel_point or "").strip()
        if not channel_point:
            raise RuntimeError("channel is required")
        params = {"channel_point": channel_point, "password": self._wallet_pw(ticker)}
        if connection_string:
            params["connection_string"] = connection_string.strip()
        return self._drpc(ticker, "request_force_close", params, timeout=120)

    def ln_add_peer(self, ticker: str, connection_string: str):
        """Connect to a Lightning peer (node_id@host:port) without opening a channel yet."""
        connection_string = (connection_string or "").strip()
        if not connection_string:
            raise RuntimeError("connection string is required")
        return self._drpc(ticker, "add_peer", {"connection_string": connection_string}, timeout=30)

    def ln_list_peers(self, ticker: str):
        try:
            return self.rpc(ticker, "list_peers")
        except Exception:
            return []

    def ln_gossip_info(self, ticker: str) -> dict:
        try:
            g = self.rpc(ticker, "gossip_info")
            return g if isinstance(g, dict) else {}
        except Exception:
            return {}

    def ln_requests(self, ticker: str):
        """Incoming Lightning payment requests (invoices you created) with paid/pending/expired state."""
        try:
            r = self.rpc(ticker, "list_requests")
            return r if isinstance(r, list) else []
        except Exception:
            return []

    def ln_delete_request(self, ticker: str, request_id: str):
        request_id = (request_id or "").strip()
        if not request_id:
            raise RuntimeError("request id is required")
        return self._drpc(ticker, "delete_request", {"request_id": request_id}, timeout=30)

    # -- network / server settings --
    def network_settings(self, ticker: str) -> dict:
        """Current network settings + the known-server list for the Settings tab."""
        info = self.getinfo(ticker)
        cur = self.daemons[ticker].server
        server = "auto" if cur == DAEMON_DEFAULT else ("offline" if cur is None else cur)
        known = []
        try:
            hostmap = self.rpc(ticker, "getservers")
            if isinstance(hostmap, dict):
                for host, ports in hostmap.items():
                    if not isinstance(ports, dict):
                        continue
                    if ports.get("s"):
                        known.append(f"{host}:{ports['s']}:s")
                    elif ports.get("t"):
                        known.append(f"{host}:{ports['t']}:t")
        except Exception:
            pass
        px = self.daemons[ticker].proxy or {}
        return {
            "server": server,                       # "auto" | "offline" | "host:port:s"
            "auto_connect": cur == DAEMON_DEFAULT,
            "connected": bool(isinstance(info, dict) and info.get("connected")),
            "live_server": info.get("server") if isinstance(info, dict) else None,
            "blockchain_height": info.get("blockchain_height") if isinstance(info, dict) else None,
            "known_servers": sorted(set(known)),
            # Proxy state for the Settings panel — never echo the password, only whether one is set.
            "proxy": {
                "enabled": bool(px.get("host")),
                "host": px.get("host", ""),
                "port": px.get("port", ""),
                "user": px.get("user", ""),
                "has_password": bool(px.get("password")),
            },
            **self.fee_settings(ticker),
        }

    # -- transaction fee policy --
    def _load_fees(self) -> Dict[str, dict]:
        fees = {t: {"mode": "network", "sat_per_byte": DEFAULT_FIXED_FEERATE} for t in self.daemons}
        try:
            with open(self._fees_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return fees
        if isinstance(saved, dict):
            for t, v in saved.items():
                if t not in fees or not isinstance(v, dict):
                    continue
                if v.get("mode") in ("network", "fixed"):
                    fees[t]["mode"] = v["mode"]
                try:
                    spb = int(v.get("sat_per_byte"))
                    if spb >= 1:
                        fees[t]["sat_per_byte"] = spb
                except (TypeError, ValueError):
                    pass
        return fees

    def _save_fees(self) -> None:
        tmp = self._fees_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._fees, f)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._fees_path)

    def fee_settings(self, ticker: str) -> dict:
        with self._fees_lock:
            f = self._fees.get(ticker) or {"mode": "network", "sat_per_byte": DEFAULT_FIXED_FEERATE}
            return {"fee_mode": f.get("mode", "network"),
                    "fee_sat_per_byte": int(f.get("sat_per_byte") or DEFAULT_FIXED_FEERATE)}

    def set_fee_policy(self, ticker: str, mode, sat_per_byte=None) -> dict:
        """Persist this coin's fee policy. mode='network' (dynamic + fixed fallback) or
        'fixed' (always sat_per_byte). No daemon restart — the rate is applied per payto."""
        if ticker not in self.daemons:
            raise RuntimeError("unknown coin")
        mode = "fixed" if str(mode) == "fixed" else "network"
        with self._fees_lock:
            cur = self._fees.setdefault(
                ticker, {"mode": "network", "sat_per_byte": DEFAULT_FIXED_FEERATE})
            cur["mode"] = mode
            if sat_per_byte is not None and str(sat_per_byte) != "":
                try:
                    spb = int(sat_per_byte)
                except (TypeError, ValueError):
                    raise RuntimeError("fee rate must be a whole number of sat/byte")
                if spb < 1:
                    raise RuntimeError("fee rate must be at least 1 sat/byte (the relay floor)")
                cur["sat_per_byte"] = spb
            self._save_fees()
        return self.network_settings(ticker)

    def _effective_feerate(self, ticker: str):
        """sat/vByte to pass to payto for the saved policy, or None to let the daemon use its
        dynamic estimate (network mode). A per-send override (passed by the caller) wins."""
        with self._fees_lock:
            f = self._fees.get(ticker) or {}
            mode, spb = f.get("mode", "network"), f.get("sat_per_byte")
        return str(spb) if (mode == "fixed" and spb) else None

    def _fallback_feerate(self, ticker: str) -> str:
        """sat/vByte for the network->fixed fallback when the server gives no estimate. Honour a
        user-set FIXED rate; otherwise use the low quiet-chain default (these chains have no fee
        market, so anything above the relay floor confirms)."""
        with self._fees_lock:
            f = self._fees.get(ticker) or {}
            mode, spb = f.get("mode"), f.get("sat_per_byte")
        if mode == "fixed" and spb:
            return str(int(spb))
        return str(NETWORK_FALLBACK_FEERATE)

    def _drpc_fee_fallback(self, ticker: str, method: str, params: dict, timeout: int = 90):
        """Build via ``_drpc``; if there's no explicit feerate and the server can't estimate fees,
        retry once at the coin's fixed fallback rate so the build succeeds instead of erroring with
        NoDynamicFeeEstimates. Other failures map to a friendly message via ``_tx_build_error``."""
        try:
            return self._drpc(ticker, method, params, timeout=timeout)
        except Exception as e:
            if "feerate" not in params and self._is_fee_estimate_error(str(e)):
                retry = dict(params)
                retry["feerate"] = self._fallback_feerate(ticker)
                try:
                    return self._drpc(ticker, method, retry, timeout=timeout)
                except Exception as e2:
                    raise RuntimeError(self._tx_build_error(str(e2)))
            raise RuntimeError(self._tx_build_error(str(e)))

    @staticmethod
    def _is_fee_estimate_error(msg: str) -> bool:
        m = (msg or "").lower()
        return ("nodynamicfeeestimates" in m or "dynamic fee" in m
                or ("fee" in m and "estimat" in m))

    def set_server(self, ticker: str, value: str) -> dict:
        """Point this coin's daemon at a server: an explicit "host:port:s", "auto" (the baked
        default + auto-connect), or "offline". The running network caches its params, so we
        restart the daemon to apply the change, then reload the (already-restored) wallet."""
        d = self.daemons[ticker]
        if value == "auto":
            d.server = DAEMON_DEFAULT
        elif value == "offline":
            d.server = None
        else:
            v = str(value).strip()
            if not v or v.startswith("-"):
                raise RuntimeError("invalid server")
            d.server = v
        was_loaded = ticker in self._loaded
        self.stop(ticker)
        self._loaded.discard(ticker)
        time.sleep(1.0)                  # let the OS release the RPC port + reap the process
        self.configure(ticker)
        self.start(ticker)               # start() only Popens — must wait for RPC readiness
        if not self.wait_ready(ticker, timeout=45):
            raise RuntimeError(f"{ticker} daemon did not come back up after the server change")
        if was_loaded:
            try:
                self.load(ticker)
            except Exception:
                pass
        return self.network_settings(ticker)

    def set_proxy(self, ticker: str, *, enable: bool, host: str = "", port: int = 0,
                  user: str = "", password: str = "") -> dict:
        """Point this coin's daemon through a SOCKS5 proxy (Tor/privacy), or turn it off. Like
        set_server, the running network caches its params, so we restart the daemon to apply it."""
        d = self.daemons[ticker]
        if enable:
            host = str(host or "").strip()
            if not host or host.startswith("-"):
                raise RuntimeError("invalid proxy host")
            try:
                port = int(port)
            except (TypeError, ValueError):
                raise RuntimeError("invalid proxy port")
            if not (1 <= port <= 65535):
                raise RuntimeError("invalid proxy port")
            # A blank password keeps the existing one; the UI never round-trips the stored secret.
            keep_pw = (d.proxy or {}).get("password", "") if not password else ""
            d.proxy = {"host": host, "port": port, "user": str(user or ""),
                       "password": str(password or keep_pw)}
        else:
            d.proxy = None
        was_loaded = ticker in self._loaded
        self.stop(ticker)
        self._loaded.discard(ticker)
        time.sleep(1.0)                  # let the OS release the RPC port + reap the process
        self.configure(ticker)
        self.start(ticker)
        if not self.wait_ready(ticker, timeout=45):
            raise RuntimeError(f"{ticker} daemon did not come back up after the proxy change")
        if was_loaded:
            try:
                self.load(ticker)
            except Exception:
                pass
        return self.network_settings(ticker)

    def getinfo(self, ticker: str) -> dict:
        # A coin the user didn't start (or stopped) has no running daemon — report it as
        # stopped without an RPC round-trip, so the dashboard greys it out cleanly.
        if self.status.get(ticker) == "stopped":
            return {"connected": False, "running": False, "status": "stopped", "server": None}
        # getinfo needs a network object; an offline (serverless) daemon can't answer it,
        # so synthesize a clear "not connected" status instead of erroring.
        if not self.is_online(ticker):
            return {"connected": False, "offline": True, "server": None}
        return self.rpc(ticker, "getinfo")

    # -- receive / send --
    def receive_address(self, ticker: str) -> Optional[str]:
        """A fresh unused receive address (better for privacy than reusing the first).
        Works offline — deriving/showing an address needs no server."""
        try:
            addr = self.rpc(ticker, "getunusedaddress")
        except Exception:
            addr = None
        if isinstance(addr, str) and addr.strip():
            return addr.strip()
        return self.first_address(ticker)

    def new_receive_address(self, ticker: str):
        """Mint a brand-new RECEIVING address (Electrum `createnewaddress` ==
        wallet.create_new_address(False)), advancing the gap so the user gets an
        address distinct from the current unused one. Falls back to the current
        unused address if the daemon can't mint a fresh one."""
        try:
            addr = self.rpc(ticker, "createnewaddress")
        except Exception:
            return None
        if isinstance(addr, str) and addr.strip():
            return addr.strip()
        return self.receive_address(ticker)   # fallback to current unused

    def can_send(self, ticker: str) -> bool:
        """Sending needs the coin online AND connected to its server (to fetch UTXOs,
        estimate the fee, and broadcast)."""
        if not self.is_online(ticker):
            return False
        info = self.getinfo(ticker)
        return bool(isinstance(info, dict) and info.get("connected"))

    def _validate_send(self, ticker: str, address, amount):
        """Shared send guards. Returns (daemon, normalized address, normalized amount)."""
        if ticker not in self.daemons:
            raise RuntimeError(f"unknown coin {ticker}")
        if not self.is_online(ticker):
            raise RuntimeError(f"{ticker} has no ElectrumX server configured yet — sending is unavailable")
        address, amount = str(address).strip(), str(amount).strip()
        # Argument-injection guard: a value beginning with '-' could be parsed as a CLI
        # option to `payto` (no valid bech32 address or amount starts with '-').
        if address.startswith("-") or amount.startswith("-"):
            raise RuntimeError("invalid address or amount")
        # Amount must be '!' (max) or a positive, FINITE decimal — reject inf/nan (which
        # would otherwise reach payto and leak a raw OverflowError) and zero/negative.
        if amount != "!":
            try:
                amt = Decimal(amount)
            except (InvalidOperation, ValueError):
                raise RuntimeError("enter a valid amount")
            if not amt.is_finite() or amt <= 0:
                raise RuntimeError("enter a valid amount")
        # Cross-coin guard: the six coins share IDENTICAL base58 P2PKH/P2SH/WIF version
        # bytes, so a legacy address cannot be attributed to a coin — only the bech32 HRP
        # disambiguates. Require the destination to be THIS coin's bech32.
        hrp = (self.coins.get(ticker) or {}).get("segwit_hrp")
        if not (hrp and address.lower().startswith(hrp + "1")):
            other = next((t for t, c in self.coins.items()
                          if t != ticker and address.lower().startswith((c.get("segwit_hrp") or "\0") + "1")), None)
            if other:
                raise RuntimeError(f"That is a {other} address\nSend {ticker} only to a {hrp}1… address")
            raise RuntimeError(f"Enter a valid {ticker} address\nIt should start with {hrp}1")
        return self.daemons[ticker], address, amount

    def _psbt_fee_and_amount(self, ticker: str, psbt: str, dest_address: str):
        """From a deserialized PSBT (inputs/outputs carry value_sats): the miner fee
        (sum inputs - sum outputs) and the amount going to the destination address."""
        j = self.rpc(ticker, "deserialize", psbt)
        if not isinstance(j, dict):
            raise RuntimeError("could not read the transaction")
        try:
            outs = j.get("outputs") or []
            ins = sum(int(i.get("value_sats") or 0) for i in (j.get("inputs") or []))
            out_total = sum(int(o.get("value_sats") or 0) for o in outs)
            amount_sat = sum(int(o.get("value_sats") or 0) for o in outs
                             if (o.get("address") or "").lower() == dest_address.lower())
        except (TypeError, ValueError, AttributeError):
            raise RuntimeError("could not read the transaction")
        fee = ins - out_total
        if fee < 0:
            raise RuntimeError("could not determine the transaction fee")
        return fee, amount_sat

    @staticmethod
    def _validate_from_coins(from_coins):
        """Validate the coin-control input list. Accepts None/empty (-> None, auto coin
        selection) or a list of ``"txid:vout"`` strings; rejects anything malformed so a
        bad selection can never reach the daemon's argv."""
        if not from_coins:
            return None
        if not isinstance(from_coins, list):
            raise RuntimeError("from_coins must be a list of \"txid:vout\"")
        out, seen = [], set()
        for c in from_coins:
            s = str(c).strip()
            txid, _, vout = s.partition(":")
            if len(txid) != 64 or not all(ch in "0123456789abcdefABCDEF" for ch in txid) \
                    or not vout.isdigit():
                raise RuntimeError(f"invalid coin reference: {s}")
            key = f"{txid.lower()}:{int(vout)}"
            if key not in seen:       # drop accidental dupes (a coin is a set-membership filter anyway)
                seen.add(key)
                out.append(key)
        return out

    def _chain_tip_height(self, ticker: str) -> int:
        try:
            info = self.rpc(ticker, "getinfo")
        except Exception:
            return 0
        if not isinstance(info, dict):
            return 0
        for key in ("blockchain_height", "server_height", "height"):
            try:
                value = int(info.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    @staticmethod
    def _utxo_key(utxo: dict) -> Optional[str]:
        txid = str(utxo.get("prevout_hash") or utxo.get("txid") or "").strip().lower()
        vout = utxo.get("prevout_n", utxo.get("vout"))
        try:
            n = int(vout)
        except (TypeError, ValueError):
            return None
        if len(txid) != 64 or not all(ch in "0123456789abcdef" for ch in txid):
            return None
        return f"{txid}:{n}"

    @staticmethod
    def _utxo_confirmations(utxo: dict, tip_height: int) -> int:
        try:
            conf = int(utxo.get("confirmations"))
        except (TypeError, ValueError):
            conf = -1
        if conf >= 0:
            return conf
        try:
            height = int(utxo.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if height <= 0 or tip_height <= 0:
            return 0
        return max(0, tip_height - height + 1)

    def _confirmed_send_coins(self, ticker: str, selected: Optional[list[str]] = None) -> list[str]:
        """Return spendable wallet outpoints that have reached the Blakestream send depth."""
        try:
            utxos = self.rpc(ticker, "listunspent") or []
        except Exception as e:
            raise RuntimeError(_friendly_send_error(str(e), selected))
        try:
            frozen = set(self.rpc(ticker, "listaddresses", "--frozen") or [])
        except Exception:
            frozen = set()
        selected_set = set(selected or [])
        selected_missing = set(selected_set)
        selected_pending: list[tuple[str, int]] = []
        selected_frozen: list[str] = []
        tip = self._chain_tip_height(ticker)
        eligible: list[str] = []
        for utxo in utxos:
            if not isinstance(utxo, dict):
                continue
            key = self._utxo_key(utxo)
            if not key:
                continue
            if selected_set:
                if key not in selected_set:
                    continue
                selected_missing.discard(key)
            if utxo.get("address") in frozen:
                if selected_set:
                    selected_frozen.append(key)
                continue
            conf = self._utxo_confirmations(utxo, tip)
            if conf >= MIN_SEND_CONFIRMATIONS:
                eligible.append(key)
            elif selected_set:
                selected_pending.append((key, conf))

        if selected_missing:
            raise RuntimeError("One or more selected coins are no longer available.")
        if selected_frozen:
            raise RuntimeError("One or more selected coins are frozen.")
        if selected_pending:
            key, conf = selected_pending[0]
            raise RuntimeError(
                f"Selected coin {key} has {conf} confirmation{'' if conf == 1 else 's'}; "
                f"Blakestream Wallet sends after {MIN_SEND_CONFIRMATIONS} confirmations."
            )
        if not eligible:
            raise RuntimeError(
                f"No spendable {ticker} coins have {MIN_SEND_CONFIRMATIONS} confirmations yet. "
                "Wait for pending receives to confirm."
            )
        return eligible

    def _build_payto(self, d, ticker: str, address, amount, feerate, from_coins=None) -> str:
        """Build the unsigned PSBT for the send. Goes through the daemon's JSON-RPC so the wallet
        password rides in the request BODY (never argv): `payto` is password-gated (@command('wp')),
        so the old CLI path failed 'Password required' on an encrypted wallet — it never reached the
        build. In network mode (feerate None) a coin whose server can't estimate raises a dynamic-fee
        error; catch that and rebuild at the coin's fixed rate (network->fixed). ``from_coins`` (a list
        of ``"txid:vout"`` from Send's coin control) restricts the inputs; the RPC body does
        ``from_coins.split(',')`` so it takes a COMMA-SEPARATED string."""
        params = {"destination": address, "amount": amount, "unsigned": True,
                  "password": self._wallet_pw(ticker)}
        if feerate is not None:
            params["feerate"] = str(feerate)
        spend_coins = self._confirmed_send_coins(ticker, from_coins)
        params["from_coins"] = ",".join(spend_coins)
        try:
            psbt = self._drpc(ticker, "payto", params, timeout=120)
        except Exception as e:
            # No explicit feerate + the server can't estimate -> retry at the coin's fixed fallback;
            # a genuine non-fee failure (e.g. insufficient funds) fails the retry too and is surfaced.
            if "feerate" not in params and self._is_fee_estimate_error(str(e)):
                retry = dict(params, feerate=self._fallback_feerate(ticker))
                try:
                    psbt = self._drpc(ticker, "payto", retry, timeout=120)
                except Exception as e2:
                    raise RuntimeError(_friendly_send_error(str(e2), from_coins))
            else:
                raise RuntimeError(_friendly_send_error(str(e), from_coins))
        if not isinstance(psbt, str) or not psbt.strip():
            raise RuntimeError("could not build the transaction")
        return psbt.strip()

    def prepare_send(self, ticker: str, address, amount, *, feerate=None, from_coins=None) -> dict:
        """Build (but do NOT broadcast) the payment and return a fee preview. The exact
        built tx is held until :meth:`confirm_send` broadcasts it, so the previewed fee
        is the fee that actually gets paid (preview == broadcast). ``from_coins`` (optional
        list of ``"txid:vout"``) restricts the inputs to the coins the user selected."""
        d, address, amount = self._validate_send(ticker, address, amount)
        from_coins = self._validate_from_coins(from_coins)
        # Drop any previously-previewed tx FIRST, so a failed (re)preview can never leave
        # a stale tx that a later confirm_send would broadcast (preview == broadcast).
        self._pending.pop(ticker, None)
        # Resolve the fee: an explicit per-send feerate (the Send-tab override) wins; otherwise
        # the coin's saved policy — 'fixed' uses its sat/byte; 'network' lets the daemon estimate
        # (no --feerate) and, if the server can't estimate, falls back to the fixed rate.
        if feerate is None:
            feerate = self._effective_feerate(ticker)   # None => dynamic estimate (network mode)
        psbt = self._build_payto(d, ticker, address, amount, feerate, from_coins=from_coins)
        fee_sat, amount_sat = self._psbt_fee_and_amount(ticker, psbt, address)
        self._pending[ticker] = psbt
        return {
            "ticker": ticker, "address": address,
            "amount_sat": amount_sat, "amount": f"{amount_sat / 1e8:.8f}",
            "fee_sat": fee_sat, "fee": f"{fee_sat / 1e8:.8f}",
            "total_sat": amount_sat + fee_sat, "total": f"{(amount_sat + fee_sat) / 1e8:.8f}",
            # flag a fee that is an unusually large slice of the amount (a hostile server's
            # inflated fee estimate) so the UI can warn before the user confirms.
            "high_fee": bool(amount_sat and fee_sat > amount_sat * 0.1),
        }

    def confirm_send(self, ticker: str) -> str:
        """Sign + broadcast the tx previewed by :meth:`prepare_send`. Returns the txid."""
        psbt = self._pending.pop(ticker, None)
        if not psbt:
            raise RuntimeError("no pending transaction to confirm — preview the send first")
        d = self.daemons[ticker]
        # signtransaction is password-gated (@command('wp')): pass the wallet password in the BODY,
        # never argv. (Calling it via the CLI without a password failed 'Password required' on an
        # encrypted wallet and leaked the unsigned PSBT into the error.)
        try:
            signed = self._drpc(ticker, "signtransaction", {"tx": psbt, "password": self._wallet_pw(ticker)})
        except Exception as e:
            raise RuntimeError(_friendly_send_error(str(e)))
        if not isinstance(signed, str) or not signed:
            raise RuntimeError("could not sign the transaction")
        b = self._run(d, "broadcast", signed, timeout=60)
        if b.returncode != 0:
            raise RuntimeError(_friendly_send_error((b.stderr or "") + "\n" + (b.stdout or "")))
        txid = (b.stdout or "").strip()
        if not txid:
            raise RuntimeError("transaction was built but the broadcast did not return a txid")
        return txid

    def send(self, ticker: str, address: str, amount, *, feerate=None, from_coins=None) -> str:
        """Build + broadcast in one step (no preview). Returns the broadcast txid."""
        self.prepare_send(ticker, address, amount, feerate=feerate, from_coins=from_coins)
        return self.confirm_send(ticker)

    def balances(self) -> Dict[str, object]:
        """Cross-coin balance aggregation (the unification point)."""
        return {t: self.rpc(t, "getbalance") for t in self.daemons}

    # -- user-configurable price sources + fiat display (see prices.py, test-api.md) --
    def _default_price_config(self) -> dict:
        # Nothing shipped/hardwired — the user adds their own named price APIs (link + jsonPath).
        return {
            "version": 1, "enabled": False,
            "poll_seconds": DEFAULT_POLL_SECONDS,
            "display": {"fiatCurrency": "USD", "displayFiat": False},
            "sources": [],
        }

    def _validate_source_spec(self, spec) -> dict:
        """Clean + validate one source (no id / apiKey here). Raises RuntimeError on bad input.
        A blank new source (empty url + jsonPath) is allowed so the user can add a row and fill
        it in; it simply yields no price until completed."""
        if not isinstance(spec, dict):
            raise RuntimeError("invalid source")
        role, kind = str(spec.get("role", "")).strip(), str(spec.get("kind", "")).strip()
        if role not in PRICE_ROLES:
            raise RuntimeError("invalid role")
        if kind not in PRICE_KINDS:
            raise RuntimeError("invalid kind")
        url, path = str(spec.get("urlTemplate") or ""), str(spec.get("jsonPath") or "")
        for tok in _PRICE_PLACEHOLDER_RE.findall(url) + _PRICE_PLACEHOLDER_RE.findall(path):
            if tok not in _PRICE_PLACEHOLDERS:
                raise RuntimeError(f"unknown placeholder {{{tok}}}")
        if path and (not _PRICE_JSONPATH_RE.match(path) or path.count(".") > 12):
            raise RuntimeError("invalid jsonPath")
        header = str(spec.get("apiKeyHeader") or "").strip()
        if header and not re.match(r"^[A-Za-z0-9._-]{1,64}$", header):
            raise RuntimeError("invalid apiKeyHeader")
        coin_ids = {}
        if isinstance(spec.get("coinIds"), dict):
            for k, v in spec["coinIds"].items():
                coin_ids[str(k).upper()] = str(v)[:64]
        try:
            ttl = int(spec.get("ttl") or 300)
        except (TypeError, ValueError):
            ttl = 300
        return {
            "role": role, "kind": kind, "enabled": bool(spec.get("enabled", True)),
            "label": str(spec.get("label") or "")[:80], "urlTemplate": url, "jsonPath": path,
            "coinIds": coin_ids, "ids": str(spec.get("ids") or "")[:64],
            "apiKeyHeader": header, "ttl": max(30, min(ttl, 86400)),
        }

    def _load_price_sources(self) -> dict:
        cfg = self._default_price_config()
        try:
            with open(self._prices_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return cfg
        if not isinstance(saved, dict):
            return cfg
        cfg["enabled"] = bool(saved.get("enabled"))
        try:
            cfg["poll_seconds"] = max(MIN_POLL_SECONDS, min(int(saved.get("poll_seconds")), MAX_POLL_SECONDS))
        except (TypeError, ValueError):
            pass
        disp = saved.get("display")
        if isinstance(disp, dict):
            fiat = str(disp.get("fiatCurrency") or "USD").upper()
            if _FIAT_RE.match(fiat):
                cfg["display"]["fiatCurrency"] = fiat
            cfg["display"]["displayFiat"] = bool(disp.get("displayFiat"))
        if isinstance(saved.get("sources"), list):
            cleaned = []
            for s in saved["sources"]:
                try:
                    c = self._validate_source_spec(s)
                except RuntimeError:
                    continue
                c["id"] = str(s.get("id") or secrets.token_hex(6))
                if s.get("apiKey"):
                    c["apiKey"] = str(s["apiKey"])
                cleaned.append(c)
            cfg["sources"] = cleaned
        return cfg

    def _save_price_sources(self) -> None:
        tmp = self._prices_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._prices, f)
        try:
            os.chmod(tmp, 0o600)   # holds API keys — owner-only
        except OSError:
            pass
        os.replace(tmp, self._prices_path)

    def _build_oracle(self) -> None:
        """(Re)build self.oracle + self._fx from self._prices. Caller holds _prices_lock
        (or single-threaded __init__). Disabled sources are skipped; order = priority."""
        cfg = self._prices
        poll = float(cfg.get("poll_seconds") or DEFAULT_POLL_SECONDS)
        providers = []
        for s in cfg.get("sources", []):
            if not s.get("enabled"):
                continue
            prov = prices.HttpTemplateProvider(
                role=s.get("role"), url_template=s.get("urlTemplate") or "",
                json_path=s.get("jsonPath") or "",
                coin_ids={k.upper(): v for k, v in (s.get("coinIds") or {}).items()},
                ids=s.get("ids") or "", api_key_header=(s.get("apiKeyHeader") or None),
                api_key=(s.get("apiKey") or None))
            providers.append(prices.CachedProvider(prov, poll))
        self.oracle = prices.PriceOracle(providers)
        self._fx = prices.FrankfurterFx()

    def _commit_prices(self) -> None:
        """Persist + rebuild the oracle. Caller holds _prices_lock."""
        self._save_price_sources()
        self._build_oracle()

    def _refresh_price_snapshot(self) -> None:
        """Compute the per-coin fiat value of ONE unit and store it. Does network (via the
        cached providers) — only ever called from the background thread or a settings mutate,
        never from portfolio()."""
        with self._prices_lock:
            enabled = bool(self._prices.get("enabled"))
            fiat = self._prices["display"]["fiatCurrency"]
            oracle, fx = self.oracle, self._fx
        if not (enabled and oracle):
            with self._snapshot_lock:
                self._price_snapshot = None
            return
        units = {}
        for t in self.daemons:
            try:
                units[t] = oracle.value_fiat(t, Decimal(1), fiat, fx)
            except Exception:
                units[t] = None
        with self._snapshot_lock:
            self._price_snapshot = {"fiat": fiat, "units": units}

    def _price_refresh_loop(self) -> None:
        while not self._stopping:
            try:
                self._refresh_price_snapshot()
            except Exception:
                pass
            with self._prices_lock:
                wait = int(self._prices.get("poll_seconds") or DEFAULT_POLL_SECONDS)
            for _ in range(max(1, wait)):   # 1s ticks so stop_all() is responsive
                if self._stopping:
                    return
                time.sleep(1)

    @staticmethod
    def _public_source(s: dict) -> dict:
        key = s.get("apiKey") or ""
        return {
            "id": s.get("id"), "role": s.get("role"), "kind": s.get("kind"),
            "enabled": bool(s.get("enabled")), "label": s.get("label", ""),
            "urlTemplate": s.get("urlTemplate", ""), "jsonPath": s.get("jsonPath", ""),
            "coinIds": s.get("coinIds", {}), "ids": s.get("ids", ""),
            "apiKeyHeader": s.get("apiKeyHeader", ""),
            "hasApiKey": bool(key),
            "apiKeyMask": ("••••" + key[-4:]) if len(key) >= 4 else ("••••" if key else ""),
            "ttl": s.get("ttl", 300),
        }

    def price_sources_public(self) -> dict:
        """The price config WITHOUT raw API keys (masked) — safe to hand the renderer."""
        with self._prices_lock:
            cfg = self._prices
            return {
                "enabled": bool(cfg.get("enabled")),
                "poll_seconds": int(cfg.get("poll_seconds") or DEFAULT_POLL_SECONDS),
                "display": dict(cfg.get("display", {})),
                "sources": [self._public_source(s) for s in cfg.get("sources", [])],
                "tickers": list(self.daemons),
            }

    def set_price_enabled(self, enabled) -> dict:
        with self._prices_lock:
            self._prices["enabled"] = bool(enabled)
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def set_poll_seconds(self, seconds) -> dict:
        try:
            n = int(seconds)
        except (TypeError, ValueError):
            raise RuntimeError("poll interval must be a whole number of seconds")
        n = max(MIN_POLL_SECONDS, min(n, MAX_POLL_SECONDS))
        with self._prices_lock:
            self._prices["poll_seconds"] = n
            self._commit_prices()   # rebuilds the oracle so cache TTL = the new interval
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def set_display_prefs(self, fiat_currency=None, display_fiat=None) -> dict:
        with self._prices_lock:
            if fiat_currency is not None:
                f = str(fiat_currency).upper()
                if not _FIAT_RE.match(f):
                    raise RuntimeError("currency must be a 3-letter code")
                self._prices["display"]["fiatCurrency"] = f
            if display_fiat is not None:
                self._prices["display"]["displayFiat"] = bool(display_fiat)
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def add_price_source(self, spec) -> dict:
        cleaned = self._validate_source_spec(spec)
        cleaned["id"] = secrets.token_hex(6)
        if spec.get("apiKey"):
            cleaned["apiKey"] = str(spec["apiKey"])
        with self._prices_lock:
            self._prices.setdefault("sources", []).append(cleaned)
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def update_price_source(self, source_id, spec) -> dict:
        cleaned = self._validate_source_spec(spec)
        with self._prices_lock:
            for s in self._prices.setdefault("sources", []):
                if s.get("id") == source_id:
                    cleaned["id"] = source_id
                    # Key: keep existing unless cleared or a new one supplied (empty = unchanged).
                    if not spec.get("clearApiKey"):
                        if s.get("apiKey"):
                            cleaned["apiKey"] = s["apiKey"]
                        if spec.get("apiKey"):
                            cleaned["apiKey"] = str(spec["apiKey"])
                    s.clear()
                    s.update(cleaned)
                    break
            else:
                raise RuntimeError("unknown source")
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def remove_price_source(self, source_id) -> dict:
        with self._prices_lock:
            self._prices["sources"] = [
                s for s in self._prices.get("sources", []) if s.get("id") != source_id]
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def set_source_enabled(self, source_id, enabled) -> dict:
        with self._prices_lock:
            for s in self._prices.get("sources", []):
                if s.get("id") == source_id:
                    s["enabled"] = bool(enabled)
                    break
            else:
                raise RuntimeError("unknown source")
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def reorder_price_sources(self, order) -> dict:
        if not isinstance(order, list):
            raise RuntimeError("order must be a list of ids")
        with self._prices_lock:
            srcs = self._prices.get("sources", [])
            by_id = {s.get("id"): s for s in srcs}
            seen = set()
            new = []
            for sid in order:
                if sid in by_id and sid not in seen:
                    new.append(by_id[sid])
                    seen.add(sid)
            new += [s for s in srcs if s.get("id") not in seen]   # safety: keep any unmentioned
            self._prices["sources"] = new
            self._commit_prices()
        self._refresh_price_snapshot()
        return self.price_sources_public()

    def test_price_source(self, spec, ticker=None, fiat=None) -> dict:
        """One-shot fetch for the Settings 'Test' button. Resolves the source for a sample
        coin/fiat and returns the extracted number (or an error), without saving anything."""
        cleaned = self._validate_source_spec(spec)
        with self._prices_lock:
            cur = self._prices["display"]["fiatCurrency"]
            stored = {s.get("id"): s.get("apiKey") for s in self._prices.get("sources", [])}
        fiat = (str(fiat).upper() if fiat else cur)
        ticker = (str(ticker).upper() if ticker else next(iter(self.daemons), "BLC"))
        role = cleaned["role"]
        api_key = spec.get("apiKey") or (stored.get(spec.get("id")) if spec.get("id") else None)
        prov = prices.HttpTemplateProvider(
            role=role, url_template=cleaned["urlTemplate"], json_path=cleaned["jsonPath"],
            coin_ids=cleaned["coinIds"], ids=cleaned["ids"],
            api_key_header=(cleaned["apiKeyHeader"] or None), api_key=api_key)
        if role == "coin_btc":
            v = prov.price_btc(ticker)
        elif role == "btc_fiat":
            v = prov.btc_fiat(fiat)
        else:
            v = prov.price_fiat(ticker, fiat)
        return {"ok": v is not None, "value": (str(v) if v is not None else None),
                "role": role, "ticker": ticker, "fiat": fiat}

    def fx_currencies(self) -> dict:
        codes = []
        try:
            if self._fx is not None:
                codes = self._fx.currencies()
        except Exception:
            codes = []
        if not codes:   # offline / blocked: a sensible static fallback list
            codes = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "CNY", "SEK", "NZD",
                     "BRL", "INR", "ZAR", "KRW", "SGD", "HKD", "MXN", "NOK", "PLN", "TRY"]
        with self._prices_lock:
            cur = self._prices["display"]["fiatCurrency"]
        return {"currencies": sorted(set(codes) | {"USD", cur})}

    @staticmethod
    def _balance_parts(bal) -> dict:
        """Split a getbalance into the displayed total and the not-yet-confirmed portion.
        amount = confirmed + unconfirmed + unmatured (so received funds always show);
        pending = unconfirmed + unmatured (surfaced as a 'pending' tag until it confirms).
        Electrum reports these separately; showing only 'confirmed' hides received-but-0-conf
        funds (e.g. on a lagging server), which reads to the user like the balance vanished."""
        if not isinstance(bal, dict):
            return {"amount": "0", "pending": "0"}

        def _sum(*keys) -> Decimal:
            tot = Decimal(0)
            for k in keys:
                v = bal.get(k)
                if v in (None, ""):
                    continue
                try:
                    tot += Decimal(str(v))
                except (InvalidOperation, ValueError, TypeError):
                    pass
            return tot

        return {
            "amount": format(_sum("confirmed", "unconfirmed", "unmatured"), "f"),
            "pending": format(_sum("unconfirmed", "unmatured"), "f"),
        }

    def _ln_capacities(self) -> dict:
        """Per-coin Lightning sendable/receivable sats for the sidebar + by-type header. Fast HTTP RPC
        with a short timeout; coins without Lightning or open channels are omitted, so this never blocks
        or bloats the 8s balance poll."""
        out = {}
        for t in self.daemons:
            try:
                cap = self._drpc(t, "lightning_capacity", {}, timeout=3)
            except Exception:
                continue
            if not isinstance(cap, dict):
                continue
            cs = int(cap.get("can_send_sat") or 0)
            cr = int(cap.get("can_receive_sat") or 0)
            nch = int(cap.get("num_channels") or 0)
            if cs or cr or nch:
                out[t] = {"ln_can_send_sat": cs, "ln_can_receive_sat": cr, "ln_channels": nch}
        return out

    def portfolio(self) -> dict:
        """Total balances across coins (confirmed + unconfirmed + unmatured), plus fiat
        valuation when the user has enabled a price source. NETWORK-FREE: fiat comes from the
        background-warmed snapshot, so a slow or dead price API can never stall this poll or
        blank a balance. Shape stays {coins, total, fiat, display_fiat, priced, unpriced}."""
        bals = {}
        for ticker in self.daemons:
            try:
                bals[ticker] = self._balance_parts(self.rpc(ticker, "getbalance"))
            except Exception:
                bals[ticker] = {"amount": "0", "pending": "0"}   # not provisioned / not ready
        ln = self._ln_capacities()
        fo = {t: dict(self._failover[t]["event"])
              for t in self.daemons if self._failover[t].get("event")}
        with self._prices_lock:
            enabled = bool(self._prices.get("enabled"))
            display_fiat = bool(self._prices["display"]["displayFiat"])
            fiat = self._prices["display"]["fiatCurrency"]
        with self._snapshot_lock:
            snap = self._price_snapshot
        if not (enabled and snap):
            return {
                "coins": {t: {"amount": b["amount"], "pending": b["pending"],
                              "synced": self._synced_cache.get(t), "value_fiat": None,
                              **ln.get(t, {})}
                          for t, b in bals.items()},
                "total": {"value_fiat": None},
                "fiat": fiat, "display_fiat": display_fiat,
                "priced": [], "unpriced": list(bals), "failover": fo,
            }
        units = snap.get("units", {})
        coins, priced, unpriced = {}, [], []
        total, any_priced = Decimal(0), False
        for t, b in bals.items():
            a = b["amount"]
            unit = units.get(t)
            vf = None
            if unit is not None:
                try:
                    vf = Decimal(str(a)) * unit
                except (InvalidOperation, TypeError, ValueError):
                    vf = None
            coins[t] = {"amount": a, "pending": b["pending"], "synced": self._synced_cache.get(t),
                        "value_fiat": (str(vf) if vf is not None else None), **ln.get(t, {})}
            if vf is not None:
                total += vf
                any_priced = True
                priced.append(t)
            else:
                unpriced.append(t)
        return {
            "coins": coins,
            "total": {"value_fiat": str(total) if any_priced else None},
            "fiat": snap.get("fiat", fiat), "display_fiat": display_fiat,
            "priced": priced, "unpriced": unpriced, "failover": fo,
        }

    def all_provisioned(self) -> bool:
        # "provisioned" = the wallet is loaded in its daemon this session (usable now),
        # not merely that a wallet file exists on disk (a relaunch starts unloaded).
        # Only the coins MEANT to be running count — deliberately-stopped coins are never
        # loaded, so they must not hold this gate open. Pre-bring-up (_active empty) falls
        # back to all daemons so it reads False until the active set is loaded.
        active = self._active or set(self.daemons)
        return bool(active) and all(t in self._loaded for t in active)

    def _verify_seed_match(self, ticker: str, mnemonic: str, passphrase: str = "") -> None:
        """Guard against a stale wallet from a DIFFERENT seed silently surviving a
        restore/create. ``provision`` no-ops when a wallet file already exists, so a
        fresh seed would never reach the daemon and the user would back up a phrase that
        does not control the displayed addresses. Compare the loaded wallet's first
        address to the one this mnemonic derives; on mismatch fail loudly."""
        expected = provisioning.provision_for_daemon(
            ticker, mnemonic, passphrase, coins=self.coins)["receive_0"]
        actual = self.first_address(ticker)
        if actual and actual != expected:
            # The wrong-seed wallet got loaded (we needed it loaded to read its address);
            # un-mark it so a retry re-attempts this coin instead of skipping it as "done".
            self._loaded.discard(ticker)
            raise RuntimeError(
                f"existing {ticker} wallet was created from a different seed "
                f"(shows {actual}, this seed derives {expected}); clear its datadir to "
                f"restore this seed")

    def _mark_coin(self, ticker: str, state: str) -> None:
        """Set ONE coin's connect state ('connecting'|'done'|'failed') in the unlock-progress
        map, thread-safe — each parallel provisioning thread updates only its own coin."""
        with self._progress_lock:
            coins = dict(self._progress.get("coins", {}))
            coins[ticker] = state
            self._progress = {**self._progress, "coins": coins}

    def _mark_detail(self, ticker: str, phase: str, server=None) -> None:
        """Human-facing sub-status for the connecting screen: the phase
        ('connecting'|'syncing'|'ready'|'failed') and the server the coin is talking to."""
        with self._progress_lock:
            detail = dict(self._progress.get("detail", {}))
            detail[ticker] = {"phase": phase, "server": server}
            self._progress = {**self._progress, "detail": detail}

    def get_progress(self) -> dict:
        with self._progress_lock:
            p = dict(self._progress)
            p["coins"] = dict(p.get("coins", {}))     # copy the nested maps for a clean read
            p["detail"] = dict(p.get("detail", {}))
            return p

    def _is_fully_synced(self, ticker: str, max_attempts: int = 40, cadence: float = 1.0) -> bool:
        """True once the wallet has connected AND fetched its address history from the server
        (electrum ``is_synchronized`` == ``wallet.is_up_to_date()``) — i.e. the balance is REAL,
        not a transient 0. ``load_wallet`` returns instantly but the network/history fetch is
        async, so we must wait for this before claiming the coin is "connected". Polls up to
        ~max_attempts*cadence seconds; a slow/dead server can't block forever (False on timeout),
        and bails immediately on shutdown.

        Primary signal is ``is_synchronized``; if that command ever errors/misbehaves we fall
        back to ``getinfo`` (connected + headers caught up) so a genuinely-connected coin is
        never falsely reported as failed."""
        sync_ok = True
        for _ in range(max_attempts):
            if self._stopping:
                return False
            if sync_ok:
                try:
                    r = self.rpc(ticker, "is_synchronized")
                    if r is True:
                        return True
                    if not isinstance(r, bool):    # unexpected shape -> stop trusting it
                        sync_ok = False
                except Exception:
                    sync_ok = False               # command unavailable -> use the fallback
            if not sync_ok:
                try:
                    info = self.rpc(ticker, "getinfo")
                    if isinstance(info, dict) and info.get("connected"):
                        bh, sh = info.get("blockchain_height"), info.get("server_height")
                        if bh is not None and sh is not None and int(bh) >= int(sh):
                            return True
                except Exception:
                    pass
            time.sleep(cadence)
        return False

    def _provision_one(self, ticker: str, mnemonic: str, passphrase: str, errors: dict) -> None:
        """Provision + load + LN-enable ONE coin. Isolated: a failure is recorded in
        `errors`/status and marks the coin 'failed' without affecting the other five."""
        if ticker in self._loaded:           # already up this session — nothing to do
            self._mark_detail(ticker, "ready", self._current_host(ticker))
            self._mark_coin(ticker, "done")
            return
        # A coin the user did NOT auto-start has no running daemon. Provision its wallet FILE
        # offline (so a later start_coin is password-free, using the in-memory session key) but
        # do not load it (no daemon to load into). Mark it 'stopped' — startable on demand.
        if ticker not in self._active:
            try:
                self.provision(ticker, mnemonic, passphrase, online=False)
            except Exception as e:
                errors[ticker] = str(e)[:200]
            self.status[ticker] = "stopped"
            self._mark_detail(ticker, "stopped", None)
            self._mark_coin(ticker, "done")   # 'done' for the progress bar = no longer connecting
            return
        try:
            self._mark_detail(ticker, "connecting", None)
            self.provision(ticker, mnemonic, passphrase, online=True)   # daemons already up
            if self.is_provisioned(ticker):
                self.load(ticker)
                self._verify_seed_match(ticker, mnemonic, passphrase)
                try:
                    self.ensure_encrypted(ticker)   # encrypt-at-rest (idempotent, crash-safe)
                except Exception:
                    pass   # wallet stays usable (rolled back to plaintext); retried next unlock
                try:
                    self.enable_lightning(ticker)   # idempotent; best-effort (on-chain works regardless)
                except Exception:
                    pass
            # "done" must mean actually connected + synced — wait for the wallet to fetch its
            # history so the user never lands on the dashboard with a transient 0 balance.
            # Offline coins (no server) can't sync; accept them as-is. A slow/dead server times
            # out -> 'failed' (honest: not ready); the dashboard poll + supervisor recover it.
            if not self.is_online(ticker):
                self._mark_detail(ticker, "ready", None)
                self._mark_coin(ticker, "done")
                return
            server = self._current_host(ticker)
            self._mark_detail(ticker, "syncing", server)
            if self._is_fully_synced(ticker):
                self._synced_cache[ticker] = True   # reflect on the dashboard immediately
                self._mark_detail(ticker, "ready", self._current_host(ticker))
                self._mark_coin(ticker, "done")
            else:
                self._mark_detail(ticker, "failed", server)
                self.status[ticker] = "failed"
                self._mark_coin(ticker, "failed")
        except Exception as e:
            errors[ticker] = str(e)[:200]
            self.status[ticker] = "failed"
            self._mark_detail(ticker, "failed", None)
            self._mark_coin(ticker, "failed")

    def provision_all(self, mnemonic: str, passphrase: str = "") -> dict:
        """Provision + load every managed coin from the shared mnemonic, ALL IN PARALLEL.
        The coins are independent (own daemon, datadir, RPC port — the daemon bring-up is
        already parallel), so wall-clock is the slowest single coin, not the sum of six.
        Idempotent: coins already loaded this session are skipped. Returns a per-coin error
        dict (empty == all good), and reports each coin's connect state via /setup/progress so
        the unlock UI can flash all six at once and settle each to solid as it lands."""
        errors: dict = {}
        tickers = list(self.daemons)
        # Derive + install the at-rest encryption keys for this unlocked session (from the seed).
        # Best-effort: if derivation ever fails we fall back to plaintext wallets rather than
        # blocking unlock. Keys live in memory only and are cleared by stop_all.
        try:
            wallet_pws, contacts_key = vault.derive_session_keys(mnemonic, tickers)
            self.set_session_keys(wallet_pws, contacts_key)
        except Exception:
            pass
        # Flash everything at once: already-loaded coins start 'done', the rest 'connecting'.
        with self._progress_lock:
            self._progress = {
                "coins": {t: ("done" if t in self._loaded else "connecting") for t in tickers},
                "detail": {t: {"phase": ("ready" if t in self._loaded else "connecting"),
                               "server": None} for t in tickers},
                "total": len(tickers),
            }
        # Seed each not-yet-loaded coin's synced flag False so the dashboard shows an honest
        # "syncing…" chip the instant the user enters early (the supervisor poll is only every
        # 8s); _provision_one flips it True the moment the coin is actually up to date.
        for t in tickers:
            if t not in self._loaded:
                self._synced_cache[t] = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tickers) or 1) as ex:
            futures = [ex.submit(self._provision_one, t, mnemonic, passphrase, errors)
                       for t in tickers]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()   # _provision_one swallows per-coin errors; this won't raise
        self.schedule_dex_announce("unlock")
        return errors

    def history(self, ticker: str) -> list:
        try:
            h = self.rpc(ticker, "onchain_history")
        except Exception:
            return []   # not provisioned / not ready
        # onchain_history returns the rows as a BARE LIST in this Electrum (4.7.2); older
        # versions wrapped them in {"transactions": [...]}. Accept both, else nothing shows.
        if isinstance(h, list):
            rows = h
        elif isinstance(h, dict):
            rows = h.get("transactions", [])
        else:
            rows = []
        if not isinstance(rows, list):
            return []
        # Normalise each row to the shape the UI expects (date/value/balance/label/...). The
        # coin-denominated columns are named per the wallet's base unit (e.g. bc_value /
        # bc_balance), NOT value/balance — so derive the signed value from the unambiguous
        # amount_sat and pick up whatever *_balance the running balance lives under. Cap at 5000.
        tip = self._chain_tip_height(ticker)
        out = []
        for t in rows[:5000]:
            if not isinstance(t, dict):
                continue
            amt = t.get("amount_sat")
            if isinstance(amt, (int, float)):
                value = f"{amt / 1e8:.8f}"
            else:
                value = t.get("value") or t.get("bc_value")
            balance = t.get("balance")
            if balance is None:
                balance = next((v for k, v in t.items() if k.endswith("_balance")), None)
            out.append({
                "txid": t.get("txid"),
                "timestamp": t.get("timestamp"),
                "date": t.get("date"),
                "value": value,
                "balance": balance,
                "height": t.get("height"),
                "confirmations": self._utxo_confirmations(t, tip),
                "label": t.get("label", ""),
            })
        return out

    def merged_history(self, limit: int = 50) -> list:
        """Cross-coin transaction feed, each entry tagged with its coin, newest first.
        Tolerant of a malformed/hostile per-coin history: skip non-dict entries and never
        let a bad timestamp type crash the sort."""
        merged = []
        for ticker in self.daemons:
            h = self.history(ticker)
            if not isinstance(h, list):
                continue
            for tx in h:
                if not isinstance(tx, dict):
                    continue
                entry = dict(tx)
                entry["coin"] = ticker
                merged.append(entry)

        def _ts(e):
            try:
                conf = int(e.get("confirmations"))
                if conf < MIN_SEND_CONFIRMATIONS:
                    return float("inf")
            except (TypeError, ValueError):
                pass
            try:
                return float(e.get("timestamp"))
            except (TypeError, ValueError):
                return 0.0
        merged.sort(key=_ts, reverse=True)
        return merged[:limit]

    # -- supervision --
    def daemon_alive(self, ticker: str, timeout: float = 10.0) -> bool:
        """Liveness via the RPC (POSIX ``daemon -d`` double-forks, so the launcher PID
        is not a reliable signal there — a reachable ``list_wallets`` is, and unlike ``getinfo`` it
        answers for offline daemons too). CRUCIAL: the CLI prints ``Error: Forbidden`` to STDOUT
        with rc=0 when a daemon is UP but rejects our rpcpassword (a foreign/old-password orphan
        squatting the fixed port). A naive rc/stdout check would call that 'alive' and adopt it, so
        start() would never reach the reaper and the wallet load would then fail. Treat an
        ``Error``-prefixed reply as NOT alive so a non-adoptable squatter is reaped, not adopted."""
        r = self._run(self.daemons[ticker], "list_wallets", timeout=timeout)
        out = r.stdout.strip()
        return r.returncode == 0 and bool(out) and not out.lower().startswith("error")

    def daemon_accepts_current_rpc(self, ticker: str, timeout: float = 2.0) -> bool:
        """True when a live daemon accepts this orchestrator's in-memory RPC password.
        Source-mode restarts can leave a detached daemon alive with the previous backend's
        rpcpassword; the CLI may still reach it via the old config, but direct JSON-RPC cannot."""
        try:
            self._drpc(ticker, "list_wallets", {}, timeout=timeout)
            return True
        except Exception:
            return False

    def ensure_running(self, ticker: str, ready_timeout: float = 45.0) -> bool:
        """Restart the coin's daemon if its RPC is unreachable, honouring a per-coin
        exponential back-off so a flapping daemon (bad server, full disk) can't spin in
        a tight restart loop. Non-blocking: a failed restart schedules the next attempt
        for a later supervisor pass rather than raising. Returns True only on a
        successful (re)start. Assumes ``configure``/``provision`` already ran once."""
        if self._stopping:                       # tearing down -> never (re)start
            return False
        if ticker not in self._active:           # deliberately stopped / never-started -> don't resurrect
            return False
        d = self.daemons[ticker]
        if self.daemon_alive(ticker):
            d.backoff, d.next_retry = 1.0, 0.0   # healthy -> reset back-off
            # Heal a coin that was slow to start: bring-up may have marked it 'failed' when
            # wait_ready timed out, but the daemon came up moments later. Reflect that in the
            # startup status so the launch screen lights it up instead of leaving it dim.
            if self.status.get(ticker) != "ready":
                self.status[ticker] = "ready"
            return False
        if time.monotonic() < d.next_retry:      # still cooling down from a failed restart
            return False
        self.start(ticker)
        if not self.wait_ready(ticker, timeout=ready_timeout):
            d.next_retry = time.monotonic() + d.backoff
            d.backoff = min(d.backoff * 2, 60.0)
            return False
        # Reload only wallets that were loaded THIS session (not merely present on disk),
        # so a crash-restart of a locked relaunch stays locked until the user unlocks.
        if ticker in self._loaded:
            self.load(ticker)
        d.backoff, d.next_retry = 1.0, 0.0
        d.restarts += 1
        self.status[ticker] = "ready"   # a restarted daemon is up — reflect it in startup status
        return True

    # -- health-aware auto-failover --
    @staticmethod
    def _dec(v) -> Decimal:
        if v in (None, ""):
            return Decimal(0)
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal(0)

    def _synced_now(self, ticker: str):
        """is_synchronized as True | False | None (None when the daemon can't answer — unknown,
        not 'unsynced')."""
        try:
            r = self.rpc(ticker, "is_synchronized")
            return r if isinstance(r, bool) else None
        except Exception:
            return None

    def _current_host(self, ticker: str) -> Optional[str]:
        try:
            info = self.rpc(ticker, "getinfo")
            return info.get("server") if isinstance(info, dict) else None
        except Exception:
            return None

    def _confirmed_positive(self, ticker: str) -> bool:
        try:
            bal = self.rpc(ticker, "getbalance")
        except Exception:
            return False
        return isinstance(bal, dict) and self._dec(bal.get("confirmed")) > 0

    def _candidate_servers(self, ticker: str) -> list:
        """Other ElectrumX servers this coin knows (host:port:s), from its recent_servers file."""
        try:
            with open(os.path.join(self.daemons[ticker].datadir, "recent_servers")) as f:
                data = json.load(f)
            return [s for s in data if isinstance(s, str) and ":" in s]
        except (OSError, ValueError):
            return []

    @staticmethod
    def _measure_latency(server_str: str, timeout: float = 1.0) -> float:
        """TCP (+TLS for ':s') connect time to an ElectrumX server, in ms; 9999 on failure.
        A cheap 'closest server' signal — connect time tracks distance + load well enough."""
        try:
            host, port, _ = server_str.split(":")
            port = int(port)
        except (ValueError, TypeError):
            return 9999.0
        t0 = time.monotonic()
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            try:
                if server_str.endswith(":s"):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    sock = ctx.wrap_socket(sock, server_hostname=host)
            finally:
                sock.close()
            return (time.monotonic() - t0) * 1000.0
        except Exception:
            return 9999.0

    def pick_best_server(self, ticker: str) -> Optional[str]:
        """Lowest-latency server this coin knows (probed in parallel), or None when it has
        fewer than two candidates (e.g. first ever run) — caller then falls back to the baked
        default. Keeps 'auto' semantics: we only set a PREFERRED server; failover still roams."""
        cands = self._candidate_servers(ticker)
        if len(cands) < 2:
            return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cands)) as ex:
            lat = dict(zip(cands, ex.map(self._measure_latency, cands)))
        best = min(cands, key=lambda s: lat[s])   # stable: ties keep recent_servers order
        return best if lat[best] < 9999.0 else None

    def _set_failover_event(self, ticker: str, state: str, server) -> None:
        """Record a user-facing failover event ('switching'|'switched'|'reverted') with a
        monotonically-increasing per-coin seq the UI dedups on (to toast each event once)."""
        st = self._failover[ticker]
        st["event"] = {"state": state, "server": server,
                       "seq": (st.get("event") or {}).get("seq", 0) + 1}

    def _failover_detect(self, ticker: str) -> None:
        """Flag a coin that's synced but 100% unconfirmed for FAILOVER_STUCK_SECONDS as a
        lagging-server suspect and fire a failover worker. AUTO MODE ONLY (never override a
        user-pinned server); honours a cooldown so it can't ping-pong."""
        if self._stopping:
            return
        d = self.daemons[ticker]
        st = self._failover[ticker]
        now = time.monotonic()
        if d.server != DAEMON_DEFAULT or st["in_flight"] or now < st["cooldown_until"]:
            st["stuck_since"] = None
            return
        if not self.daemon_alive(ticker):
            st["stuck_since"] = None
            return
        try:
            bal = self.rpc(ticker, "getbalance")
        except Exception:
            return
        if not isinstance(bal, dict):
            st["stuck_since"] = None
            return
        confirmed = self._dec(bal.get("confirmed"))
        pending = self._dec(bal.get("unconfirmed")) + self._dec(bal.get("unmatured"))
        if not (confirmed == 0 and pending > 0 and self._synced_now(ticker)):
            st["stuck_since"] = None
            return
        if st["stuck_since"] is None:
            st["stuck_since"] = now
            return
        if now - st["stuck_since"] >= FAILOVER_STUCK_SECONDS:
            st["in_flight"] = True
            threading.Thread(target=self._failover_worker, args=(ticker,),
                             name=f"failover-{ticker}", daemon=True).start()

    def _failover_worker(self, ticker: str) -> None:
        """Try each OTHER known server once; keep the first that reports a CONFIRMED balance (the
        lagging-index server was the problem). If none helps (funds genuinely unconfirmed, or every
        server lags), revert to auto and cool down so we don't keep flapping."""
        lock = self._failover_locks[ticker]
        if not lock.acquire(blocking=False):
            self._failover[ticker]["in_flight"] = False
            return
        try:
            st = self._failover[ticker]
            tried_hosts = {self._current_host(ticker)} | {s.split(":")[0] for s in st["tried"]}
            cands = [s for s in self._candidate_servers(ticker) if s.split(":")[0] not in tried_hosts]
            for srv in cands[:2]:
                if self._stopping:
                    return
                st["tried"].add(srv)
                host = srv.split(":")[0]
                self._set_failover_event(ticker, "switching", host)
                try:
                    self.set_server(ticker, srv)            # restart onto this server
                except Exception:
                    continue
                self._is_fully_synced(ticker, max_attempts=FAILOVER_SYNC_GRACE, cadence=1.0)
                if self._confirmed_positive(ticker):
                    self._set_failover_event(ticker, "switched", host)
                    st["stuck_since"] = None                # success — keep this healthier server
                    return
            # nothing better — back to auto + cooldown; clear 'tried' so a later pass can retry
            try:
                self.set_server(ticker, "auto")
            except Exception:
                pass
            self._set_failover_event(ticker, "reverted", None)
            st["tried"].clear()
            st["cooldown_until"] = time.monotonic() + FAILOVER_COOLDOWN
            st["stuck_since"] = None
        finally:
            self._failover[ticker]["in_flight"] = False
            lock.release()

    def supervise_once(self) -> list:
        """One supervision pass over all managed daemons; returns those restarted.
        A caller polls this on an interval (with its own back-off if desired). Idle until
        the initial bring-up has run (so it can't race configure/provision) and after a
        deliberate shutdown.

        NOTE: the index-lag health-failover is DISABLED. Its premise ("synced but 100%
        unconfirmed == lagging server") cannot be distinguished from funds that are GENUINELY
        unconfirmed (still in the mempool, not yet mined) without a cross-server address-history
        probe — and on these slow chains the latter is common, so the failover churned daemons
        (restart -> other server also 0-conf -> revert -> repeat) for funds no switch can fix.
        Ping-based 'closest server' selection (configure) + dead-daemon restart still apply."""
        if self._stopping or not self._supervision_enabled:
            return []
        # Only supervise coins MEANT to be running (self._active); deliberately-stopped /
        # never-started coins have no daemon and must not be auto-restarted or RPC-probed.
        # Skip ensure_running for a coin whose failover is in flight — the worker owns its daemon.
        restarted = [t for t in self.daemons
                     if t in self._active
                     and not self._failover[t]["in_flight"] and self.ensure_running(t)]
        # Refresh each coin's is_synchronized flag so the dashboard can show 'syncing' vs 'pending'.
        now = time.monotonic()
        if now - self._last_synced_poll >= SYNCED_POLL_INTERVAL:
            self._last_synced_poll = now
            for t in self.daemons:
                if t not in self._active:
                    continue
                try:
                    self._synced_cache[t] = self._synced_now(t)
                except Exception:
                    self._synced_cache[t] = None
        return restarted

    # -- shutdown --
    def stop(self, ticker: str) -> None:
        d = self.daemons[ticker]
        try:
            self._run(d, "stop", timeout=20)
        except Exception:
            pass
        if d.proc and d.proc.poll() is None:
            try:
                d.proc.wait(timeout=10)
            except Exception:
                d.proc.kill()

    def stop_all(self) -> None:
        # Quiesce the supervisor FIRST: once this is set, ensure_running/supervise_once and
        # start() all no-op, so a 5s supervisor tick can't resurrect a daemon we just stopped
        # (which would orphan it to init). Set before stopping any daemon.
        self._stopping = True
        self.clear_session_keys()   # drop the in-memory wallet/contacts keys on shutdown
        for ticker in list(self.daemons):
            self.stop(ticker)

    # -- on-demand per-coin start/stop (post-startup) --
    def _coin_state(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "status": self.status.get(ticker),
            "running": self.daemon_alive(ticker),
            "loaded": ticker in self._loaded,
            "needs_unlock": self.is_provisioned(ticker) and self.locked(),
        }

    def _load_started_coin_wallet(self, ticker: str) -> None:
        """Load an already-provisioned coin wallet into a running daemon for this unlocked session.
        On-demand starts can race with an already-running detached daemon; the daemon being alive is
        not enough if its wallet list is still empty."""
        if ticker in self._loaded or not self.is_provisioned(ticker) or self.locked():
            return
        if self._wallet_is_encrypted(ticker):
            self.load(ticker)
        else:
            self._checked(self.daemons[ticker], "load_wallet", timeout=30)
            self._loaded.add(ticker)
            self.ensure_encrypted(ticker)

    def _announce_coin_change(self, event: str, ticker: str) -> None:
        """If a DEX is paired, audit + re-announce so the DEX picks up/drops this coin.
        Fire-and-forget: the announce worker is async and its inflight guard debounces rapid
        toggles; never blocks the start/stop call."""
        with self._dex_integration_lock:
            if not (self._dex_integration.get("allow_local_dex")
                    and self._normalize_dex_id(self._dex_integration.get("trusted_dex_id"))):
                return
        self._record_dex_funding_audit({"event": event, "ticker": ticker})
        self.schedule_dex_announce(event, require_startup_auto=False)

    def start_coin(self, ticker: str, ready_timeout: float = 45.0) -> dict:
        """Start a coin's daemon ON DEMAND (post-startup) and load its already-provisioned wallet
        using the in-memory session key — no vault password needed. Marks the coin active so the
        supervisor keeps it alive, then re-announces to a connected DEX. Idempotent; start() is
        self-guarded + idempotent so this needs no outer lock."""
        if ticker not in self.daemons:
            raise KeyError(ticker)
        self._active.add(ticker)          # before any wait: a supervisor tick now keeps it alive
        if self.daemon_accepts_current_rpc(ticker):
            try:
                self._load_started_coin_wallet(ticker)
            except Exception as e:
                self.status[ticker] = "failed"
                raise RuntimeError(f"{ticker} daemon is running but wallet could not be loaded: {e}") from e
            self.status[ticker] = "ready"
            self._announce_coin_change("coin-start", ticker)
            return self._coin_state(ticker)
        self.configure(ticker)
        self.status[ticker] = "starting"
        d = self.daemons[ticker]
        d.backoff, d.next_retry = 1.0, 0.0   # fresh start, not a back-off restart
        self.start(ticker)
        if not self.wait_ready(ticker, timeout=ready_timeout):
            self.status[ticker] = "failed"    # left active -> supervisor retries with back-off
            raise RuntimeError(f"{ticker} daemon did not become ready in {ready_timeout}s")
        self._inject_ln_fees(ticker)
        # Load the already-provisioned wallet with the in-memory session password (no mnemonic).
        # If the session is soft-locked (no keys), the daemon stays up but unloaded until unlock.
        try:
            self._load_started_coin_wallet(ticker)
        except Exception as e:
            self.status[ticker] = "failed"
            raise RuntimeError(f"{ticker} daemon started but wallet could not be loaded: {e}") from e
        if ticker in self._loaded:
            threading.Thread(target=self._connect_ln_hub, args=(ticker,), name=f"ln-hub-{ticker}", daemon=True).start()
        self.status[ticker] = "ready"
        self._synced_cache[ticker] = self._synced_now(ticker)
        self._announce_coin_change("coin-start", ticker)
        return self._coin_state(ticker)

    def stop_coin(self, ticker: str, force: bool = False) -> dict:
        """Stop ONE coin's daemon on demand and mark it inactive so the supervisor won't
        resurrect it. The wallet file stays on disk (startable again later). When the DEX is
        connected, refuse unless force=True (the UI confirms), then announce the change FIRST so
        the DEX cancels/withdraws this coin's orders before its daemon goes away."""
        if ticker not in self.daemons:
            raise KeyError(ticker)
        with self._dex_integration_lock:
            dex_live = self._dex_connected_locked()
            dex_paired = bool(self._dex_integration.get("allow_local_dex")
                              and self._normalize_dex_id(self._dex_integration.get("trusted_dex_id")))
        if dex_live and not force:
            raise DexOrdersActiveError(ticker)
        self._active.discard(ticker)      # gate the supervisor BEFORE stopping
        if dex_paired:
            self._record_dex_funding_audit({"event": "coin-stop", "ticker": ticker})
            self.schedule_dex_announce("coin-stop", require_startup_auto=False)   # DEX reconciles via /dex/ready (now stopped)
        d = self.daemons[ticker]
        with self._start_guard(d):        # serialize vs an in-flight supervisor (re)start
            self.stop(ticker)
        self._loaded.discard(ticker)
        self.status[ticker] = "stopped"
        self._synced_cache[ticker] = None
        return self._coin_state(ticker)
