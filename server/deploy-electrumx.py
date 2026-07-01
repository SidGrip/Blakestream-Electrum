#!/usr/bin/env python3
"""
Discover local BlakeStream daemons and generate/start an ElectrumX deployment.

The generated compose file intentionally keeps daemon RPC credentials local to
the deployment directory and never prints them to stdout.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CoinSpec:
    ticker: str
    service: str
    coin_name: str
    default_datadir: str
    conf_name: str
    default_rpc_port: int
    tcp_port: int
    ssl_port: int
    admin_rpc_port: int
    process_terms: tuple[str, ...]


COINS: tuple[CoinSpec, ...] = (
    CoinSpec("BLC", "blc", "Blakecoin", ".blakecoin", "blakecoin.conf", 8772, 50001, 50002, 8001, ("blakecoind", "blakecoin", "blc")),
    CoinSpec("BBTC", "bbtc", "BlakeBitcoin", ".blakebitcoin", "blakebitcoin.conf", 8243, 50011, 50012, 8002, ("blakebitcoind", "blakebitcoin", "bbtc")),
    CoinSpec("ELT", "elt", "Electron-ELT", ".electron", "electron.conf", 6852, 50021, 50022, 8003, ("electrond", "electron", "elt")),
    CoinSpec("LIT", "lit", "Lithium", ".lithium", "lithium.conf", 12000, 50031, 50032, 8004, ("lithiumd", "lithium", "lit")),
    CoinSpec("PHO", "pho", "Photon", ".photon", "photon.conf", 8984, 50041, 50042, 8005, ("photond", "photon", "pho")),
    CoinSpec("UMO", "umo", "UniversalMolecule", ".universalmolecule", "universalmolecule.conf", 5921, 50051, 50052, 8006, ("universalmoleculed", "universalmolecule", "umo")),
)


@dataclass
class RpcProbe:
    ok: bool
    blocks: int | None = None
    headers: int | None = None
    ibd: bool | None = None
    txindex_synced: bool | None = None
    txindex_height: int | None = None
    error: str | None = None


@dataclass
class Candidate:
    spec: CoinSpec
    kind: str
    source: str
    config_path: str
    rpc_host: str
    rpc_port: int
    conf: dict[str, str]
    probe: RpcProbe

    @property
    def daemon_url(self) -> str:
        user = self.conf["rpcuser"]
        password = self.conf["rpcpassword"]
        return f"http://{user}:{password}@{self.rpc_host}:{self.rpc_port}/"

    @property
    def redacted_url(self) -> str:
        return f"http://***:***@{self.rpc_host}:{self.rpc_port}/"


def run(cmd: list[str], *, check: bool = False, text: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text)


def read_conf_text(text: str) -> dict[str, str]:
    conf: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        conf[key.strip()] = value.strip()
    return conf


def read_conf_file(path: Path) -> dict[str, str]:
    try:
        return read_conf_text(path.read_text())
    except OSError:
        return {}


def rpc_call(host: str, port: int, user: str, password: str, method: str, timeout: float = 5.0) -> object:
    payload = json.dumps({"jsonrpc": "1.0", "id": "probe", "method": method, "params": []}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/",
        data=payload,
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        parsed = json.loads(resp.read().decode())
    if parsed.get("error"):
        raise RuntimeError(parsed["error"])
    return parsed.get("result")


def probe_rpc(host: str, port: int, conf: dict[str, str]) -> RpcProbe:
    user = conf.get("rpcuser")
    password = conf.get("rpcpassword")
    if not user or not password:
        return RpcProbe(False, error="missing rpcuser/rpcpassword")
    try:
        chain = rpc_call(host, port, user, password, "getblockchaininfo")
        if not isinstance(chain, dict):
            return RpcProbe(False, error="getblockchaininfo returned an unexpected response")
        indexes = rpc_call(host, port, user, password, "getindexinfo")
        if not isinstance(indexes, dict):
            return RpcProbe(False, error="getindexinfo returned an unexpected response")
        tx = indexes.get("txindex")
        if not isinstance(tx, dict):
            return RpcProbe(False, error="getindexinfo did not report txindex")
        txindex_synced = bool(tx.get("synced"))
        best = tx.get("best_block_height")
        txindex_height = int(best) if best is not None else None
        return RpcProbe(
            True,
            blocks=int(chain.get("blocks")) if chain.get("blocks") is not None else None,
            headers=int(chain.get("headers")) if chain.get("headers") is not None else None,
            ibd=bool(chain.get("initialblockdownload")),
            txindex_synced=txindex_synced,
            txindex_height=txindex_height,
        )
    except (OSError, RuntimeError, urllib.error.URLError, socket.timeout) as exc:
        return RpcProbe(False, error=str(exc))


def parse_process_datadirs(spec: CoinSpec) -> list[Path]:
    proc = run(["ps", "-eo", "args"])
    if proc.returncode != 0:
        return []
    results: list[Path] = []
    pattern = re.compile(r"(?:^|\s)-datadir=(\"[^\"]+\"|'[^']+'|[^\s]+)")
    for line in proc.stdout.splitlines():
        lowered = line.lower()
        if not any(term in lowered for term in spec.process_terms):
            continue
        match = pattern.search(line)
        if not match:
            continue
        raw = match.group(1).strip("\"'")
        path = Path(raw).expanduser()
        if path not in results:
            results.append(path)
    return results


def native_candidates(spec: CoinSpec, home: Path) -> list[tuple[str, Path, dict[str, str], str, int]]:
    datadirs = [home / spec.default_datadir]
    datadirs.extend(parse_process_datadirs(spec))
    seen: set[Path] = set()
    results: list[tuple[str, Path, dict[str, str], str, int]] = []
    for datadir in datadirs:
        conf_path = (datadir / spec.conf_name).resolve()
        if conf_path in seen:
            continue
        seen.add(conf_path)
        conf = read_conf_file(conf_path)
        if not conf:
            continue
        port = int(conf.get("rpcport", spec.default_rpc_port))
        results.append(("native", conf_path, conf, "127.0.0.1", port))
    return results


def docker_json(cmd: list[str]) -> object | None:
    proc = run(cmd)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def docker_running_containers() -> list[dict[str, object]]:
    proc = run(["docker", "ps", "--format", "{{json .}}"])
    if proc.returncode != 0:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def docker_exec_text(container: str, command: str) -> str | None:
    proc = run(["docker", "exec", container, "sh", "-lc", command])
    if proc.returncode != 0:
        return None
    return proc.stdout


def docker_container_conf(spec: CoinSpec, container: str, inspect_data: dict[str, object]) -> tuple[str, dict[str, str]] | None:
    shell_paths = [
        f"$HOME/{spec.default_datadir}/{spec.conf_name}",
        f"/root/{spec.default_datadir}/{spec.conf_name}",
        f"/home/*/{spec.default_datadir}/{spec.conf_name}",
        f"/data/{spec.conf_name}",
        f"/config/{spec.conf_name}",
        f"/wallet/{spec.conf_name}",
    ]
    quoted = " ".join(shell_paths)
    found = docker_exec_text(container, f"for f in {quoted}; do [ -f \"$f\" ] && echo \"$f\" && exit 0; done; exit 1")
    if found:
        conf_path = found.strip().splitlines()[0]
        text = docker_exec_text(container, f"cat {sh_quote(conf_path)}")
        if text:
            conf = read_conf_text(text)
            if conf:
                return (f"{container}:{conf_path}", conf)

    for mount in inspect_data.get("Mounts", []) or []:
        if not isinstance(mount, dict):
            continue
        src = mount.get("Source")
        dst = mount.get("Destination", "")
        if not src or not isinstance(src, str):
            continue
        source_path = Path(src)
        options = []
        if isinstance(dst, str) and dst.endswith(spec.default_datadir):
            options.append(source_path / spec.conf_name)
        options.append(source_path / spec.conf_name)
        for path in options:
            conf = read_conf_file(path)
            if conf:
                return (str(path), conf)
    return None


def docker_rpc_host_port(spec: CoinSpec, conf: dict[str, str], inspect_data: dict[str, object]) -> tuple[str, int] | None:
    port = int(conf.get("rpcport", spec.default_rpc_port))
    net = inspect_data.get("NetworkSettings", {})
    if isinstance(net, dict):
        ports = net.get("Ports", {})
        if isinstance(ports, dict):
            mappings = ports.get(f"{port}/tcp")
            if mappings:
                first = mappings[0]
                host_ip = first.get("HostIp") or "127.0.0.1"
                host_port = int(first.get("HostPort") or port)
                if host_ip in {"0.0.0.0", "::", ""}:
                    host_ip = "127.0.0.1"
                return (host_ip, host_port)
        networks = net.get("Networks", {})
        if isinstance(networks, dict):
            for details in networks.values():
                if isinstance(details, dict):
                    ip = details.get("IPAddress")
                    if ip:
                        return (str(ip), port)
    return None


def docker_candidates(spec: CoinSpec) -> list[tuple[str, Path, dict[str, str], str, int]]:
    results: list[tuple[str, Path, dict[str, str], str, int]] = []
    for row in docker_running_containers():
        cid = str(row.get("ID", ""))
        name = str(row.get("Names", ""))
        image = str(row.get("Image", ""))
        haystack = f"{name} {image}".lower()
        if not any(term in haystack for term in spec.process_terms):
            continue
        inspected = docker_json(["docker", "inspect", cid])
        if not isinstance(inspected, list) or not inspected:
            continue
        inspect_data = inspected[0]
        if not isinstance(inspect_data, dict):
            continue
        found = docker_container_conf(spec, cid, inspect_data)
        if not found:
            continue
        conf_path, conf = found
        address = docker_rpc_host_port(spec, conf, inspect_data)
        if not address:
            continue
        host, port = address
        results.append((f"docker:{name}", Path(conf_path), conf, host, port))
    return results


def env_candidates(spec: CoinSpec) -> list[tuple[str, Path, dict[str, str], str, int]]:
    key = f"DAEMON_URL_{spec.ticker}"
    value = os.environ.get(key, "").strip()
    if not value:
        return []
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or not parsed.hostname:
        return [
            (
                "env",
                Path(f"env:{key}"),
                {"_error": "expected http://USER:PASS@HOST:PORT/"},
                "127.0.0.1",
                spec.default_rpc_port,
            )
        ]
    conf = {
        "rpcuser": urllib.parse.unquote(parsed.username or ""),
        "rpcpassword": urllib.parse.unquote(parsed.password or ""),
        "txindex": "1",
    }
    return [("env", Path(f"env:{key}"), conf, parsed.hostname, parsed.port or spec.default_rpc_port)]


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def discover(spec: CoinSpec, *, home: Path) -> tuple[Candidate | None, list[Candidate]]:
    candidates: list[Candidate] = []
    raw_candidates = env_candidates(spec)
    raw_candidates.extend(native_candidates(spec, home))
    raw_candidates.extend(docker_candidates(spec))
    for kind, conf_path, conf, host, port in raw_candidates:
        probe = probe_rpc(host, port, conf)
        candidates.append(Candidate(spec, kind, kind, str(conf_path), host, port, conf, probe))
    valid = [c for c in candidates if c.probe.ok]
    for candidate in valid:
        if candidate.kind == "env":
            return candidate, candidates
    for candidate in valid:
        if candidate.kind == "native":
            return candidate, candidates
    if valid:
        return valid[0], candidates
    return None, candidates


def yaml_string(value: str) -> str:
    return json.dumps(value)


def render_compose(
    candidates: list[Candidate],
    *,
    image: str,
    report_host: str,
    port_offset: int,
    ssl_enabled: bool,
    fee_fallback: str,
) -> str:
    lines = [
        "# Generated by server/deploy-electrumx.py",
        "# Contains daemon RPC credentials; keep this file private.",
        "services:",
    ]
    for candidate in candidates:
        spec = candidate.spec
        tcp = spec.tcp_port + port_offset
        ssl = spec.ssl_port + port_offset
        admin = spec.admin_rpc_port + port_offset
        services = [f"tcp://0.0.0.0:{tcp}", f"rpc://127.0.0.1:{admin}"]
        report_services = [f"tcp://{report_host}:{tcp}"]
        if ssl_enabled:
            services.insert(0, f"ssl://0.0.0.0:{ssl}")
            report_services.insert(0, f"ssl://{report_host}:{ssl}")
        lines.extend(
            [
                f"  {spec.service}:",
                f"    image: {yaml_string(image)}",
                "    restart: unless-stopped",
                "    network_mode: host",
                "    stop_grace_period: 10m",
                "    environment:",
                f"      COIN: {yaml_string(spec.coin_name)}",
                "      NET: mainnet",
                "      DB_ENGINE: leveldb",
                "      ALLOW_ROOT: \"true\"",
                "      PEER_DISCOVERY: self",
                "      DB_DIRECTORY: /db",
                f"      DAEMON_URL: {yaml_string(candidate.daemon_url)}",
                f"      SERVICES: {yaml_string(','.join(services))}",
                f"      REPORT_SERVICES: {yaml_string(','.join(report_services))}",
            ]
        )
        if fee_fallback:
            lines.append(f"      ELECTRUMX_DEFAULT_FEE_BTC_KVB: {yaml_string(fee_fallback)}")
        if ssl_enabled:
            lines.extend(
                [
                    "      SSL_CERTFILE: /ssl/fullchain.pem",
                    "      SSL_KEYFILE: /ssl/privkey.pem",
                ]
            )
        lines.extend(
            [
                "    volumes:",
                f"      - ./db/{spec.service}:/db",
            ]
        )
        if ssl_enabled:
            lines.append("      - ./ssl:/ssl:ro")
    return "\n".join(lines) + "\n"


def write_summary(path: Path, candidates: list[Candidate], *, port_offset: int, ssl_enabled: bool) -> None:
    rows = [
        "# Blakestream ElectrumX deploy summary",
        "",
        "No RPC credentials are written in this summary.",
        "",
        "| Coin | Source | Config | RPC | Blocks | IBD | txindex | Electrum TCP | Electrum SSL |",
        "|------|--------|--------|-----|--------|-----|---------|--------------|--------------|",
    ]
    for c in candidates:
        txindex = "unknown"
        if c.probe.txindex_synced is True:
            txindex = f"synced {c.probe.txindex_height}"
        elif c.probe.txindex_synced is False:
            txindex = "not synced or disabled"
        rows.append(
            f"| {c.spec.ticker} | {c.kind} | `{c.config_path}` | `{c.redacted_url}` | "
            f"{c.probe.blocks} | {c.probe.ibd} | {txindex} | "
            f"{c.spec.tcp_port + port_offset} | "
            f"{c.spec.ssl_port + port_offset if ssl_enabled else 'off'} |"
        )
    path.write_text("\n".join(rows) + "\n")


def compose_cmd() -> list[str]:
    if run(["docker", "compose", "version"]).returncode == 0:
        return ["docker", "compose"]
    if run(["docker-compose", "version"]).returncode == 0:
        return ["docker-compose"]
    raise SystemExit("docker compose is not available")


def electrum_server_version(port: int) -> bool:
    request = {"id": "ready", "method": "server.version", "params": ["blakestream-deploy", "1.4"]}
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.settimeout(8)
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        data = sock.recv(8192)
    if not data:
        return False
    response = json.loads(data.decode("utf-8"))
    return response.get("id") == "ready" and "result" in response


def wait_ready(candidates: list[Candidate], *, port_offset: int, timeout: int) -> None:
    if timeout <= 0:
        return
    pending = {c.spec.ticker: c.spec.tcp_port + port_offset for c in candidates}
    started = time.time()
    while pending and time.time() - started < timeout:
        for ticker, port in list(pending.items()):
            try:
                if electrum_server_version(port):
                    print(f"{ticker}: Electrum JSON-RPC ready on TCP {port}")
                    pending.pop(ticker, None)
            except (OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
                pass
        if pending:
            time.sleep(10)
    if pending:
        pending_labels = ", ".join(f"{ticker}:{port}" for ticker, port in pending.items())
        raise SystemExit(f"ElectrumX service(s) did not answer server.version within {timeout}s: {pending_labels}")


def validate_port_offset(specs: Iterable[CoinSpec], port_offset: int) -> None:
    if port_offset < 0:
        raise SystemExit("--port-offset must be 0 or greater")
    invalid: list[str] = []
    for spec in specs:
        for label, base in (("tcp", spec.tcp_port), ("ssl", spec.ssl_port), ("admin", spec.admin_rpc_port)):
            port = base + port_offset
            if port < 1 or port > 65535:
                invalid.append(f"{spec.ticker} {label}={port}")
    if invalid:
        raise SystemExit("--port-offset produces invalid port(s): " + ", ".join(invalid))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover BlakeStream daemons and deploy ElectrumX.")
    parser.add_argument("--image", default="sidgrip/electrumx-blakestream:25.2")
    parser.add_argument("--build-image", action="store_true", help="Build the ElectrumX image with ../build-electrumx.sh --smoke --no-push first.")
    parser.add_argument("--deploy-dir", default="server/deploy", help="Directory for generated compose, DBs, ssl, and summary.")
    parser.add_argument("--report-host", default=socket.getfqdn() or "electrum1.blakestream.io")
    parser.add_argument("--coins", default="all", help="Comma-separated tickers, or all.")
    parser.add_argument("--port-offset", type=int, default=0, help="Add this value to Electrum TCP/SSL/admin ports. Useful for tests.")
    parser.add_argument("--ssl", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--dry-run", action="store_true", help="Discover and validate only; do not write compose files.")
    parser.add_argument("--start", action="store_true", help="Run docker compose up -d after writing the deployment.")
    parser.add_argument("--wait-ready", type=int, default=0, help="Seconds to wait for Electrum TCP ports after --start.")
    parser.add_argument("--allow-unsynced-txindex", action="store_true", help="Generate compose even if a daemon txindex is not synced yet.")
    parser.add_argument("--allow-ibd", action="store_true", help="Generate compose even if a daemon is still in initial block download. Intended for staging only.")
    parser.add_argument("--fee-fallback", default=os.environ.get("ELECTRUMX_DEFAULT_FEE_BTC_KVB", ""), help="Optional ELECTRUMX_DEFAULT_FEE_BTC_KVB value to include in generated services.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    deploy_dir = (repo_root / args.deploy_dir).resolve() if not Path(args.deploy_dir).is_absolute() else Path(args.deploy_dir)
    selected = {c.ticker.lower(): c for c in COINS}
    if args.coins != "all":
        wanted = {x.strip().lower() for x in args.coins.split(",") if x.strip()}
        unknown = sorted(wanted - set(selected))
        if unknown:
            raise SystemExit(f"unknown coin ticker(s): {', '.join(unknown)}")
        specs = [selected[t] for t in selected if t in wanted]
    else:
        specs = list(COINS)
    validate_port_offset(specs, args.port_offset)

    if args.build_image:
        build_script = repo_root / "build-electrumx.sh"
        print(f"Building image {args.image} with {build_script}")
        subprocess.run([str(build_script), args.image, "--smoke", "--no-push"], check=True)

    ssl_dir = deploy_dir / "ssl"
    ssl_files_present = (ssl_dir / "fullchain.pem").exists() and (ssl_dir / "privkey.pem").exists()
    ssl_enabled = args.ssl == "on" or (args.ssl == "auto" and ssl_files_present)
    if args.ssl == "on" and not ssl_files_present and not args.dry_run:
        raise SystemExit(f"--ssl on requires {ssl_dir}/fullchain.pem and privkey.pem")

    chosen: list[Candidate] = []
    failures = 0
    print("Daemon discovery:")
    for spec in specs:
        winner, candidates = discover(spec, home=Path.home())
        if not candidates:
            print(f"  {spec.ticker}: no native or Docker RPC config found")
            failures += 1
            continue
        if winner is None:
            print(f"  {spec.ticker}: config found but RPC probe failed")
            for candidate in candidates:
                print(f"    - {candidate.kind} {candidate.config_path}: {candidate.probe.error}")
            failures += 1
            continue
        tx_ok = winner.probe.txindex_synced is True
        if not tx_ok and not args.allow_unsynced_txindex:
            print(f"  {spec.ticker}: RPC ok, but txindex is not synced/enabled; fix daemon before deploy")
            failures += 1
            continue
        if winner.probe.ibd is True and not args.allow_ibd:
            blocks = winner.probe.blocks or 0
            headers = winner.probe.headers or 0
            if blocks <= 0 or headers <= 0 or abs(headers - blocks) > 2:
                print(f"  {spec.ticker}: RPC ok, but daemon is still behind headers; wait for sync before deploy")
                failures += 1
                continue
        chosen.append(winner)
        tx_label = "unknown"
        if winner.probe.txindex_synced is True:
            tx_label = f"synced at {winner.probe.txindex_height}"
        elif winner.probe.txindex_synced is False:
            tx_label = "not synced"
        print(
            f"  {spec.ticker}: {winner.kind} {winner.redacted_url} "
            f"blocks={winner.probe.blocks} ibd={winner.probe.ibd} txindex={tx_label}"
        )

    if failures:
        print(f"Discovery failed for {failures} coin(s). No deployment written.")
        return 2
    if args.dry_run:
        print("Dry run complete; no files written.")
        return 0

    deploy_dir.mkdir(parents=True, exist_ok=True)
    (deploy_dir / "db").mkdir(exist_ok=True)
    ssl_dir.mkdir(exist_ok=True)
    compose = render_compose(
        chosen,
        image=args.image,
        report_host=args.report_host,
        port_offset=args.port_offset,
        ssl_enabled=ssl_enabled,
        fee_fallback=args.fee_fallback,
    )
    compose_path = deploy_dir / "docker-compose.yml"
    compose_path.write_text(compose)
    os.chmod(compose_path, 0o600)
    write_summary(deploy_dir / "deploy-summary.md", chosen, port_offset=args.port_offset, ssl_enabled=ssl_enabled)
    print(f"Wrote {compose_path}")
    print(f"Wrote {deploy_dir / 'deploy-summary.md'}")
    if not ssl_enabled:
        print("SSL is off for this generated deployment. Add ssl/fullchain.pem and ssl/privkey.pem, then rerun with --ssl on for public production SSL.")
    if args.start:
        cmd = compose_cmd() + ["-f", str(compose_path), "up", "-d"]
        print("Starting ElectrumX services with docker compose")
        subprocess.run(cmd, check=True)
    if args.wait_ready:
        wait_ready(chosen, port_offset=args.port_offset, timeout=args.wait_ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
