"""P2 orchestrator integration smoke.

Run on a host that has the generated per-coin workspaces (e.g. the build server
192.168.1.221 under /mnt/ram-build/wsroot). Brings up all six daemons from ONE test
mnemonic via the Orchestrator and verifies each daemon's first receive address
matches the known-answer vector, then shuts everything down.

This is an integration smoke (needs live workspaces + a reachable server so the
daemons start), not a unit test — keep it out of the default pytest run.

Usage on .221:
    ELECTRUM_PYBIN=/mnt/ram-build/venv-electrum/bin/python \
    ELECTRUM_WSROOT=/mnt/ram-build/wsroot \
    ELECTRUM_DDROOT=/mnt/ram-build/odd \
    python -m unified.p2_smoke
"""

import os
import sys

from unified.orchestrator import Orchestrator, DEFAULT_RPC_PORTS, DAEMON_DEFAULT

TEST_MNEMONIC = ("abandon abandon abandon abandon abandon abandon "
                 "abandon abandon abandon abandon abandon about")

# receive[0] (m/84'/<coin_type>'/0'/0/0, BIP84 native segwit) for TEST_MNEMONIC, per coin.
KAV = {
    "BLC": "blc1ql27pg0ttv2pvdcqe06dw220epn5yxj64p89800",
    "BBTC": "bbtc1qlyg3wuu3zyw85ulz2my7wqh2rndtrufz2g4jdp",
    "ELT": "elt1qjammm4pj40cvmqhwpj3jj0pcj0w3r2ds9z943m",
    "LIT": "lit1qv9e6sqrlvuxge76s95lzy549034ewmpum9rlv9",
    "PHO": "pho1q4l6rh9wedm5w7jz2ph9s3nwtxuh9zl8hkv2mf8",
    "UMO": "umo1qnk09lsphnthwcyhr6km63ug5vzhweh9aksgup3",
}

# Each coin starts online against its own baked default (electrum1/electrum2 via the
# variant's bundled servers.json + NETWORK_SERVER default). Override per coin with
# ELECTRUM_SERVER_<TICKER> (an explicit host:port:s, or empty string to force offline).


def main() -> int:
    pybin = os.environ.get("ELECTRUM_PYBIN", sys.executable)
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("set ELECTRUM_WSROOT to the generated-workspaces root", file=sys.stderr)
        return 2
    ddroot = os.environ.get("ELECTRUM_DDROOT", "/tmp/electrum-p2-smoke")
    servers = {t: os.environ.get(f"ELECTRUM_SERVER_{t}", DAEMON_DEFAULT)
               for t in DEFAULT_RPC_PORTS}

    orch = Orchestrator(python_bin=pybin, workspaces_root=wsroot,
                        datadirs_root=ddroot, servers=servers)
    failures = []
    try:
        orch.bring_up_all(TEST_MNEMONIC)
        for ticker in DEFAULT_RPC_PORTS:
            addr = orch.first_address(ticker)
            ok = addr == KAV[ticker]
            print(f"{ticker:4} :{orch.daemons[ticker].rpc_port}  {addr}  MATCH={ok}")
            if not ok:
                failures.append(ticker)
    finally:
        orch.stop_all()

    if failures:
        print(f"FAIL: address mismatch for {failures}")
        return 1
    print("PASS: 6/6 first addresses match the known-answer vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
