#!/usr/bin/env python3
"""Electrium Hub coordinator for the six generated Blakestream variants.

The Hub deliberately keeps one isolated Electrium runtime per coin. It is a
thin launcher/coordinator, not a rewrite of Electrum's global network context.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
COINS_JSON = REPO_ROOT / "coin-overlays" / "coins.json"
DEFAULT_OUTPUTS = REPO_ROOT / "outputs"
DEFAULT_WORKSPACES = REPO_ROOT / "build" / "workspaces"
DEFAULT_STATE_DIR = Path.home() / ".electrium-hub"
DEFAULT_MANIFEST = Path.home() / ".blakestream" / "electrium-hub" / "wallets.json"
DEFAULT_RPC_PORTS = {
    "BLC": 57101,
    "BBTC": 57102,
    "ELT": 57103,
    "LIT": 57104,
    "PHO": 57105,
    "UMO": 57106,
}
NETWORK_FLAGS = {
    "testnet": "--testnet",
    "regnet": "--regtest",
}
NETWORK_CONFIG_DIRS = {
    "testnet": "testnet",
    "regnet": "regtest",
}


def load_coins() -> dict[str, dict[str, Any]]:
    with COINS_JSON.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def coin_or_die(coins: dict[str, dict[str, Any]], ticker: str) -> dict[str, Any]:
    try:
        return coins[ticker.upper()]
    except KeyError as exc:
        raise SystemExit(f"unknown coin ticker: {ticker}") from exc


def appimage_path(ticker: str, coin: dict[str, Any], outputs: Path) -> Path:
    return outputs / ticker / "linux" / f"{coin['app_name']}-4.7.2-x86_64.AppImage"


def data_dir_path(coin: dict[str, Any]) -> Path:
    configured = os.environ.get(str(coin["env_var"]))
    if configured:
        return Path(configured).expanduser()
    return Path.home() / str(coin["data_dir_unix"])


def config_path(coin: dict[str, Any], network: str | None = None) -> Path:
    base = data_dir_path(coin)
    if network in NETWORK_CONFIG_DIRS:
        return base / NETWORK_CONFIG_DIRS[network] / "config"
    return base / "config"


def pid_path(ticker: str, state_dir: Path) -> Path:
    return state_dir / "pids" / f"{ticker.upper()}.pid"


def log_path(ticker: str, state_dir: Path) -> Path:
    return state_dir / "logs" / f"{ticker.upper()}.log"


def manifest_path() -> Path:
    configured = os.environ.get("BLAKESTREAM_ELECTRIUM_HUB_MANIFEST")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_MANIFEST


def write_manifest(rows: list[dict[str, Any]]) -> Path:
    path = manifest_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "version": "25.2",
        "generatedAt": int(time.time()),
        "variants": rows,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def default_rpc_port(ticker: str) -> int:
    try:
        return DEFAULT_RPC_PORTS[ticker.upper()]
    except KeyError as exc:
        raise SystemExit(f"missing Hub RPC port for {ticker}") from exc


def read_config(coin: dict[str, Any], ticker: str, network: str | None = None) -> dict[str, Any]:
    path = config_path(coin, network)
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "dataDir": str(path.parent),
            "network": network or "default",
            "rpcHost": "127.0.0.1",
            "rpcPort": default_rpc_port(ticker),
            "rpcUser": "user",
            "hasRpcPassword": False,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "exists": True,
            "path": str(path),
            "dataDir": str(path.parent),
            "network": network or "default",
            "error": f"invalid config JSON: {exc}",
            "rpcHost": "127.0.0.1",
            "rpcPort": default_rpc_port(ticker),
            "rpcUser": "user",
            "hasRpcPassword": False,
        }
    rpc_port = raw.get("rpcport")
    if not isinstance(rpc_port, int) or rpc_port <= 0 or rpc_port > 65535:
        rpc_port = default_rpc_port(ticker)
    rpc_user = raw.get("rpcuser")
    if not isinstance(rpc_user, str) or not rpc_user:
        rpc_user = "user"
    rpc_password = raw.get("rpcpassword")
    return {
        "exists": True,
        "path": str(path),
        "dataDir": str(path.parent),
        "network": network or "default",
        "rpcHost": "127.0.0.1",
        "rpcPort": rpc_port,
        "rpcUser": rpc_user,
        "hasRpcPassword": isinstance(rpc_password, str) and len(rpc_password) > 0,
        "rpcPassword": rpc_password if isinstance(rpc_password, str) and rpc_password else None,
    }


def ensure_one_config(
    coin: dict[str, Any],
    ticker: str,
    network: str | None = None,
    canonical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = config_path(coin, network)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"cannot update invalid config for {ticker}: {path}: {exc}") from exc
    changed = False
    existing_user = data.get("rpcuser")
    existing_password = data.get("rpcpassword")
    existing_port = data.get("rpcport")
    target_user = canonical.get("rpcUser") if canonical else existing_user
    target_password = canonical.get("rpcPassword") if canonical else existing_password
    target_port = canonical.get("rpcPort") if canonical else existing_port
    if not isinstance(target_user, str) or not target_user:
        target_user = "user"
    if not isinstance(target_password, str) or not target_password:
        target_password = secrets.token_urlsafe(24)
    if not isinstance(target_port, int) or target_port <= 0 or target_port > 65535:
        target_port = default_rpc_port(ticker)

    if data.get("rpcuser") != target_user:
        data["rpcuser"] = target_user
        changed = True
    if data.get("rpcpassword") != target_password:
        data["rpcpassword"] = target_password
        changed = True
    if data.get("rpcport") != target_port:
        data["rpcport"] = target_port
        changed = True
    if changed or not path.exists():
        path.write_text(json.dumps(data, indent=4, sort_keys=True) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return read_config(coin, ticker, network)


def ensure_config(coin: dict[str, Any], ticker: str, network: str | None = None) -> dict[str, Any]:
    root = ensure_one_config(coin, ticker)
    if network:
        return ensure_one_config(coin, ticker, network, root)
    for network_name in NETWORK_CONFIG_DIRS:
        ensure_one_config(coin, ticker, network_name, root)
    return root


def read_pid(ticker: str, state_dir: Path) -> int | None:
    path = pid_path(ticker, state_dir)
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def port_conflict_message(host: str, port: int) -> str:
    return f"RPC port already in use: {host}:{port}"


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    public_cfg = dict(cfg)
    public_cfg.pop("rpcPassword", None)
    return public_cfg


def variant_status(
    ticker: str,
    coin: dict[str, Any],
    outputs: Path,
    workspaces: Path,
    state_dir: Path,
) -> dict[str, Any]:
    appimage = appimage_path(ticker, coin, outputs)
    workspace = workspaces / ticker
    cfg = read_config(coin, ticker)
    pid = read_pid(ticker, state_dir)
    pid_is_running = pid_running(pid)
    rpc_port = int(cfg.get("rpcPort") or default_rpc_port(ticker))
    rpc_host = str(cfg.get("rpcHost") or "127.0.0.1")
    errors = []
    if not appimage.exists():
        errors.append(f"missing AppImage: {appimage}")
    if not cfg.get("exists"):
        errors.append(f"missing config: {cfg['path']} (run ensure-config)")
    if not cfg.get("hasRpcPassword"):
        errors.append("missing rpcpassword (run ensure-config)")
    rpc_port_open = port_open(rpc_host, rpc_port)
    running = pid_is_running or rpc_port_open
    return {
        "ticker": ticker,
        "coinName": coin["coin_name"],
        "appName": coin["app_name"],
        "dataDirUnix": coin["data_dir_unix"],
        "envVar": coin["env_var"],
        "mainnetHrp": coin["segwit_hrp"],
        "testnetHrp": coin["testnet_segwit_hrp"],
        "regtestHrp": coin["regtest_segwit_hrp"],
        "appimage": str(appimage),
        "appimageExists": appimage.exists(),
        "workspace": str(workspace),
        "workspaceExists": workspace.exists(),
        "config": public_config(cfg),
        "runtime": {
            "pid": pid,
            "running": running,
            "pidRunning": pid_is_running,
            "pidFile": str(pid_path(ticker, state_dir)),
            "logFile": str(log_path(ticker, state_dir)),
        },
        "hubRpc": {
            "host": rpc_host,
            "port": rpc_port,
            "portOpen": rpc_port_open,
        },
        "supportedNetworks": ["testnet", "regnet"],
        "mainnetEnabled": False,
        "custody": "native",
        "runtimeIsolation": "per-coin process and per-coin data dir",
        "errors": errors,
    }


def list_variants(args: argparse.Namespace) -> int:
    coins = load_coins()
    rows = [
        variant_status(ticker, coin, args.outputs, args.workspaces, args.state_dir)
        for ticker, coin in sorted(coins.items())
    ]
    print(json.dumps({"version": "25.2", "walletShape": "unified-shell", "variants": rows}, indent=2))
    return 0


def selected_coin_items(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    coins = load_coins()
    ticker = getattr(args, "ticker", None)
    if ticker:
        upper = ticker.upper()
        return [(upper, coin_or_die(coins, upper))]
    return sorted(coins.items())


def status_variants(args: argparse.Namespace) -> int:
    rows = [
        variant_status(ticker, coin, args.outputs, args.workspaces, args.state_dir)
        for ticker, coin in selected_coin_items(args)
    ]
    print(json.dumps({"version": "25.2", "variants": rows}, indent=2))
    return 0


def config_variants(args: argparse.Namespace) -> int:
    rows = []
    manifest_rows = []
    for ticker, coin in selected_coin_items(args):
        cfg = ensure_config(coin, ticker) if args.ensure else read_config(coin, ticker)
        private_row = {
            "ticker": ticker,
            "coinName": coin["coin_name"],
            "appName": coin["app_name"],
            "config": cfg,
        }
        manifest_rows.append(private_row)
        public_cfg = dict(cfg) if args.include_password else public_config(cfg)
        rows.append({
            "ticker": ticker,
            "coinName": coin["coin_name"],
            "appName": coin["app_name"],
            "config": public_cfg,
        })
    payload: dict[str, Any] = {"version": "25.2", "variants": rows}
    if args.ensure:
        payload["manifest"] = str(write_manifest(manifest_rows))
    print(json.dumps(payload, indent=2))
    return 0


def build_variant(args: argparse.Namespace) -> int:
    ticker = args.ticker.upper()
    coin_or_die(load_coins(), ticker)
    cmd = [
        str(REPO_ROOT / "scripts" / "build_wallet_variant.sh"),
        ticker,
        args.target,
        str(args.workspaces),
        str(args.outputs),
    ]
    return subprocess.call(cmd, cwd=str(REPO_ROOT), env=os.environ)


def launch_variant(args: argparse.Namespace) -> int:
    ticker = args.ticker.upper()
    coin = coin_or_die(load_coins(), ticker)
    appimage = appimage_path(ticker, coin, args.outputs)
    if not appimage.exists():
        if args.detach:
            print(json.dumps({"ok": False, "ticker": ticker, "error": f"missing AppImage: {appimage}"}, indent=2))
            return 1
        raise SystemExit(f"missing AppImage for {ticker}: {appimage}")
    cfg = ensure_config(coin, ticker, args.network)
    rpc_host = str(cfg.get("rpcHost") or "127.0.0.1")
    rpc_port = int(cfg.get("rpcPort") or default_rpc_port(ticker))
    pid = read_pid(ticker, args.state_dir)
    if pid_running(pid):
        error = f"{ticker} already running with pid {pid}"
        if args.detach:
            print(json.dumps({"ok": False, "ticker": ticker, "error": error, "pid": pid}, indent=2))
            return 1
        raise SystemExit(error)
    if port_open(rpc_host, rpc_port) and not pid_running(pid):
        error = port_conflict_message(rpc_host, rpc_port)
        if args.detach:
            print(json.dumps({"ok": False, "ticker": ticker, "error": error}, indent=2))
            return 1
        raise SystemExit(error)
    env = dict(os.environ)
    env.setdefault(coin["env_var"], str(Path.home() / coin["data_dir_unix"]))
    variant_args = list(args.variant_args)
    if variant_args and variant_args[0] == "--":
        variant_args = variant_args[1:]
    network_flag = NETWORK_FLAGS.get(args.network)
    cmd = [str(appimage)]
    if network_flag:
        cmd.append(network_flag)
    cmd.extend(variant_args)
    if not args.detach:
        return subprocess.call(cmd, cwd=str(REPO_ROOT), env=env)

    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (args.state_dir / "pids").mkdir(mode=0o700, parents=True, exist_ok=True)
    (args.state_dir / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
    log_file = log_path(ticker, args.state_dir)
    with log_file.open("ab") as log:
        child = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path(ticker, args.state_dir).write_text(f"{child.pid}\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "ticker": ticker,
        "pid": child.pid,
        "network": args.network,
        "cmd": cmd,
        "config": {k: v for k, v in cfg.items() if k != "rpcPassword"},
        "logFile": str(log_file),
    }, indent=2))
    return 0


def stop_variant(args: argparse.Namespace) -> int:
    ticker = args.ticker.upper()
    coin = coin_or_die(load_coins(), ticker)
    appimage = appimage_path(ticker, coin, args.outputs)
    cfg = ensure_config(coin, ticker, args.network)
    rpc_host = str(cfg.get("rpcHost") or "127.0.0.1")
    rpc_port = int(cfg.get("rpcPort") or default_rpc_port(ticker))
    env = dict(os.environ)
    env.setdefault(coin["env_var"], str(Path.home() / coin["data_dir_unix"]))
    stop_stdout = ""
    stop_stderr = ""
    if appimage.exists() and port_open(rpc_host, rpc_port):
        network_flag = NETWORK_FLAGS.get(args.network)
        cmd = [str(appimage)]
        if network_flag:
            cmd.append(network_flag)
        cmd.append("stop")
        stopped = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        stop_stdout = stopped.stdout.strip()
        stop_stderr = stopped.stderr.strip()
    pid = read_pid(ticker, args.state_dir)
    if pid and pid_running(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not pid_running(pid) and not port_open(rpc_host, rpc_port):
            break
        time.sleep(0.2)
    running = pid_running(pid) or port_open(rpc_host, rpc_port)
    if not running:
        try:
            pid_path(ticker, args.state_dir).unlink()
        except OSError:
            pass
    print(json.dumps({
        "ok": not running,
        "ticker": ticker,
        "pid": pid,
        "running": running,
        "network": args.network,
        "rpc": {"host": rpc_host, "port": rpc_port},
        "stdout": stop_stdout,
        "stderr": stop_stderr,
    }, indent=2))
    return 0 if not running else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--workspaces", type=Path, default=DEFAULT_WORKSPACES)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    status = sub.add_parser("status")
    status.add_argument("ticker", nargs="?")

    config = sub.add_parser("config")
    config.add_argument("ticker", nargs="?")
    config.add_argument("--include-password", action="store_true")
    config.set_defaults(ensure=False, include_password=False)

    ensure = sub.add_parser("ensure-config")
    ensure.add_argument("ticker", nargs="?")
    ensure.add_argument("--include-password", action="store_true")
    ensure.set_defaults(ensure=True)

    build = sub.add_parser("build")
    build.add_argument("ticker")
    build.add_argument("--target", choices=("wheel", "appimage", "both"), default="wheel")

    launch = sub.add_parser("launch")
    launch.add_argument("ticker")
    launch.add_argument("--detach", action="store_true")
    launch.add_argument("--network", choices=("testnet", "regnet"), default="testnet")
    launch.add_argument("variant_args", nargs=argparse.REMAINDER)

    stop = sub.add_parser("stop")
    stop.add_argument("--network", choices=("testnet", "regnet"), default="testnet")
    stop.add_argument("ticker")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return list_variants(args)
    if args.cmd == "status":
        return status_variants(args)
    if args.cmd in ("config", "ensure-config"):
        return config_variants(args)
    if args.cmd == "build":
        return build_variant(args)
    if args.cmd == "launch":
        return launch_variant(args)
    if args.cmd == "stop":
        return stop_variant(args)
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
