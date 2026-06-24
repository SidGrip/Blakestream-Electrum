"""Multiwallet private-key portability check.

This is an integration check for the 0.25.2 unified wallet workspaces. It proves
that private keys derived/exported from the shared multiwallet seed can be
imported by each standalone single-coin Electrum wallet.

Run after generating the six variant workspaces:

    ELECTRUM_WSROOT=/path/to/workspaces \
    ELECTRUM_PYBIN=/path/to/python \
    PYTHONPATH=/path/to/Blakestream-Electrium-0.25.2 \
      python unified/tests/private_key_interop_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from unified import provisioning

CANONICAL = ("abandon abandon abandon abandon abandon abandon "
             "abandon abandon abandon abandon abandon about")
SECOND = ("legal winner thank year wave sausage worth useful "
          "legal winner thank yellow")

N_RECV = 2
N_CHANGE = 1


def _norm_path(path: str) -> str:
    return path.replace("h", "'")

SINGLE_SNIPPET = r'''
import json, os, sys, tempfile
import electrum
from electrum import bitcoin, constants, keystore, util
from electrum.simple_config import SimpleConfig
from electrum.wallet import Wallet, restore_wallet_from_text
from electrum.wallet_db import WalletDB

try:
    constants.set_mainnet()
except Exception:
    pass
util.create_and_start_event_loop()

mnemonic = sys.argv[1]
expected_json = json.loads(sys.argv[2])
n_recv, n_change = int(sys.argv[3]), int(sys.argv[4])

root_seed = keystore.bip39_to_seed(mnemonic, passphrase="")
der = keystore.bip44_derivation(0, bip43_purpose=84)
ks = keystore.from_bip43_rootseed(root_seed, derivation=der, xtype="p2wpkh")

config = SimpleConfig({"electrum_path": tempfile.mkdtemp()})
db = WalletDB("", storage=None, upgrade=True)
db.put("keystore", ks.dump())
db.put("wallet_type", "standard")
db.put("gap_limit", max(n_recv, 5))
db.put("gap_limit_for_change", max(n_change, 5))
w = Wallet(db, config=config)
w.synchronize()

recv = list(w.get_receiving_addresses()[:n_recv])
change = list(w.get_change_addresses()[:n_change])
if recv != expected_json["receive"] or change != expected_json["change"]:
    raise AssertionError({
        "single_recv": recv,
        "multi_recv": expected_json["receive"],
        "single_change": change,
        "multi_change": expected_json["change"],
    })

checks = []
for label, addr in [("receive0", recv[0]), ("change0", change[0])]:
    exported = w.export_private_key(addr, password=None)
    derived_addr = bitcoin.address_from_private_key(exported)
    if derived_addr != addr:
        raise AssertionError(f"address_from_private_key mismatch for {label}: {derived_addr} != {addr}")

    imported = restore_wallet_from_text(
        exported,
        path=None,
        config=SimpleConfig({"electrum_path": tempfile.mkdtemp()}),
        encrypt_file=False,
        gap_limit=1,
        gap_limit_for_change=1,
    )["wallet"]
    imported_addresses = list(imported.get_addresses())
    if addr not in imported_addresses:
        raise AssertionError(f"imported wallet missing {label} address {addr}: {imported_addresses}")
    roundtrip = imported.export_private_key(addr, password=None)
    if roundtrip != exported:
        raise AssertionError(f"WIF roundtrip mismatch for {label}: {roundtrip} != {exported}")
    checks.append({
        "label": label,
        "address": addr,
        "imported_addresses": imported_addresses,
        "wif_prefix": exported.split(":", 1)[0] if ":" in exported else "",
        "wif_tail": exported[-8:],
    })

sys.stdout.write("JSON:" + json.dumps({
    "coin_type": constants.net.BIP44_COIN_TYPE,
    "hrp": constants.net.SEGWIT_HRP,
    "derivation": der,
    "recv": recv,
    "change": change,
    "checks": checks,
    "electrum_file": os.path.abspath(electrum.__file__),
}) + "\n")
sys.stdout.flush()
os._exit(0)
'''


def single_check(pybin: str, workspace: str, mnemonic: str, expected: dict) -> dict:
    env = dict(os.environ, PYTHONPATH=workspace)
    proc = subprocess.run(
        [pybin, "-c", SINGLE_SNIPPET, mnemonic, json.dumps(expected), str(N_RECV), str(N_CHANGE)],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"private-key check failed in {workspace}:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("JSON:"):
            return json.loads(line[len("JSON:"):])
    raise RuntimeError(f"no JSON from private-key check in {workspace}:\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    pybin = os.environ.get("ELECTRUM_PYBIN", sys.executable)
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("set ELECTRUM_WSROOT to the generated-variant-workspaces root", file=sys.stderr)
        return 2

    coins = provisioning.load_coins()
    ok_all = True
    for mnemonic, label in ((CANONICAL, "CANONICAL"), (SECOND, "SECOND")):
        print(f"\n=== mnemonic: {label} ===")
        multi = provisioning.derive_all(mnemonic, num_receive=N_RECV, num_change=N_CHANGE)
        for ticker in coins:
            workspace = os.path.join(wsroot, ticker)
            expected = {
                "receive": multi[ticker].receive,
                "change": multi[ticker].change,
            }
            try:
                got = single_check(pybin, workspace, mnemonic, expected)
            except Exception as e:
                print(f"  {ticker:4} [FAIL] {e}")
                ok_all = False
                continue
            isolated = os.path.abspath(workspace) in got.get("electrum_file", "")
            path_ok = _norm_path(got["derivation"]) == f"m/84'/{got['coin_type']}'/0'"
            checks_ok = len(got["checks"]) == 2
            ok = isolated and path_ok and checks_ok
            ok_all = ok_all and ok
            print(f"  {ticker:4} [{'PASS' if ok else 'FAIL'}] "
                  f"coin_type={got['coin_type']} hrp={got['hrp']} path={got['derivation']}")
            for check in got["checks"]:
                print(f"        {check['label']}: {check['address']} "
                      f"imported={check['imported_addresses'][0]} "
                      f"wif={check['wif_prefix']}:...{check['wif_tail']}")
            print(f"        electrum={got.get('electrum_file', '?')} isolated={isolated}")
            if not path_ok:
                print("        PATH FAIL: derivation did not match coin_type")
            if not checks_ok:
                print("        CHECK FAIL: receive/change import checks missing")

    print("\n" + ("PASS: multiwallet private keys import into every standalone coin wallet."
                  if ok_all else
                  "FAIL: at least one private-key portability check failed."))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
