"""Cross-restore interchangeability check: single-coin wallet  ==  multiwallet seed.

Proves the BIP84 promise end-to-end: ONE BIP39 mnemonic, when restored into each
standalone single-coin Electrum wallet (BIP39 + native segwit, exactly as the wizard
does it), yields the SAME addresses as the unified multiwallet derives for that coin.

For each coin and each test mnemonic it compares three independently-computed sources:

  1. SINGLE  - the real single-coin wallet engine in that coin's variant workspace.
               Builds an actual ``Standard_Wallet`` from a BIP32 keystore at
               ``m/84'/<coin_type>'/0'`` (xtype p2wpkh) via the wizard's own code path
               (``keystore.bip44_derivation(0, 84)`` reads ``constants.net`` of THAT
               variant) and reads ``get_receiving_addresses`` / ``get_change_addresses``.
  2. MULTI   - ``unified.provisioning.derive_all`` (net-free, coin_type from coins.json).
  3. KAV     - the frozen known-answer receive[0] vectors (canonical mnemonic only).

PASS == single.recv == multi.recv  AND  single.change == multi.change  for all coins,
and (for the canonical mnemonic) recv[0] == KAV.  The single side runs as a subprocess
inside each variant workspace because ``constants.net`` is a process-global singleton
(one process can only host one coin's constants).

This is an integration check (needs the 6 generated variant workspaces), not a unit test
- keep it out of the default pytest run.  Run on a build host that has the workspaces:

    rsync -a unified coin-overlays <build-host>:/path/to/xrestore/
    ssh <build-host> \
      ELECTRUM_WSROOT=/path/to/wsroot \
      ELECTRUM_PYBIN=/path/to/venv/bin/python \
      PYTHONPATH=/path/to/xrestore:/path/to/wsroot/BLC \
      /path/to/venv/bin/python \
        /path/to/xrestore/unified/tests/cross_restore_check.py
"""

import json
import os
import subprocess
import sys

from unified import provisioning

# Canonical BIP39 test vector (Trezor all-zero entropy), a second independent
# 12-word vector, and a 24-word all-zero vector so a pass cannot be an artifact
# of one seed length or one entropy shape.
CANONICAL = ("abandon abandon abandon abandon abandon abandon "
             "abandon abandon abandon abandon abandon about")
SECOND = ("legal winner thank year wave sausage worth useful "
          "legal winner thank yellow")
CANONICAL_24 = ("abandon abandon abandon abandon abandon abandon "
                "abandon abandon abandon abandon abandon abandon "
                "abandon abandon abandon abandon abandon abandon "
                "abandon abandon abandon abandon abandon art")
PASSPHRASE = "BlakeStream QA 25.2 !@# MixedCase 1234567890"
WRONG_PASSPHRASE = "wrong " + PASSPHRASE

# Frozen known-answer receive[0] for CANONICAL, per coin (also in unified/p2_smoke.py).
# Provenance: independently reproduced by three non-electrum BIP84 implementations
# (bip_utils, embit, and a from-scratch pure-Python secp256k1/BIP32/bech32), each
# calibrated to BIP84's own published vector
# (m/84'/0'/0'/0/0 -> bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu) and then producing
# each coin's address by changing only coin_type + HRP. So these are objectively
# correct, not self-referential to the wallet code they gate.
KAV_RECEIVE0 = {
    "BLC": "blc1ql27pg0ttv2pvdcqe06dw220epn5yxj64p89800",
    "BBTC": "bbtc1qlyg3wuu3zyw85ulz2my7wqh2rndtrufz2g4jdp",
    "ELT": "elt1qjammm4pj40cvmqhwpj3jj0pcj0w3r2ds9z943m",
    "LIT": "lit1qv9e6sqrlvuxge76s95lzy549034ewmpum9rlv9",
    "PHO": "pho1q4l6rh9wedm5w7jz2ph9s3nwtxuh9zl8hkv2mf8",
    "UMO": "umo1qnk09lsphnthwcyhr6km63ug5vzhweh9aksgup3",
}

# Snippet run inside each variant workspace: restore the mnemonic the way the
# single-coin wallet's "BIP39 seed -> native segwit" wizard choice does, build a real
# wallet, and emit the addresses it would actually display.
SINGLE_SNIPPET = r'''
import json, os, sys, tempfile
import electrum
from electrum import bitcoin, constants, keystore, util
from electrum.wallet import Wallet
from electrum.wallet_db import WalletDB
from electrum.simple_config import SimpleConfig
try:
    constants.set_mainnet()
except Exception:
    pass
# Wallet.__init__ -> synchronize() fires an asyncio callback; needs the loop running.
util.create_and_start_event_loop()
mnemonic, passphrase = sys.argv[1], sys.argv[2]
n_recv, n_change = int(sys.argv[3]), int(sys.argv[4])
root_seed = keystore.bip39_to_seed(mnemonic, passphrase=passphrase)
# This is exactly what the Qt wizard sets for native segwit (wallet.py: bip44_derivation(0, 84)).
der = keystore.bip44_derivation(0, bip43_purpose=84)
ks = keystore.from_bip43_rootseed(root_seed, derivation=der, xtype="p2wpkh")
# (a) the REAL wallet object: read the addresses it would actually display.
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
# (b) independent cross-check: derive straight from the keystore (the wallet's own
# address formula) and assert the real wallet agrees with it.
recv_ks = [bitcoin.pubkey_to_address("p2wpkh", ks.derive_pubkey(0, i).hex()) for i in range(n_recv)]
change_ks = [bitcoin.pubkey_to_address("p2wpkh", ks.derive_pubkey(1, i).hex()) for i in range(n_change)]
assert recv == recv_ks, f"wallet vs keystore recv mismatch: {recv} != {recv_ks}"
assert change == change_ks, f"wallet vs keystore change mismatch: {change} != {change_ks}"
sys.stdout.write("JSON:" + json.dumps({
    "coin_type": constants.net.BIP44_COIN_TYPE,
    "hrp": constants.net.SEGWIT_HRP,
    "derivation": der,
    "recv": recv,
    "change": change,
    # provenance: which electrum package this subprocess actually loaded, so a
    # green run documents that each coin used its OWN variant (not a shared default).
    "electrum_file": os.path.abspath(electrum.__file__),
}) + "\n")
sys.stdout.flush()
# The event-loop thread is non-daemon and would keep this process alive; hard-exit.
os._exit(0)
'''

N_RECV = int(os.environ.get("ELECTRUM_XRESTORE_RECV", "100"))
N_CHANGE = int(os.environ.get("ELECTRUM_XRESTORE_CHANGE", "20"))


def single_addresses(pybin, workspace, mnemonic, passphrase):
    env = dict(os.environ, PYTHONPATH=workspace)
    proc = subprocess.run(
        [pybin, "-c", SINGLE_SNIPPET, mnemonic, passphrase, str(N_RECV), str(N_CHANGE)],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"single restore failed in {workspace}:\n{proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("JSON:"):
            return json.loads(line[len("JSON:"):])
    raise RuntimeError(f"no JSON from single restore in {workspace}:\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    pybin = os.environ.get("ELECTRUM_PYBIN", sys.executable)
    wsroot = os.environ.get("ELECTRUM_WSROOT")
    if not wsroot:
        print("set ELECTRUM_WSROOT to the generated-variant-workspaces root", file=sys.stderr)
        return 2

    coins = provisioning.load_coins()
    overall_ok = True

    cases = (
        (CANONICAL, "", "CANONICAL"),
        (SECOND, "", "SECOND"),
        (CANONICAL_24, "", "CANONICAL_24"),
        (CANONICAL, PASSPHRASE, "CANONICAL_WITH_PASSPHRASE"),
    )

    for mnemonic, passphrase, label in cases:
        multi = provisioning.derive_all(
            mnemonic, passphrase=passphrase, num_receive=N_RECV, num_change=N_CHANGE)
        wrong_multi = None
        if passphrase:
            wrong_multi = provisioning.derive_all(
                mnemonic, passphrase=WRONG_PASSPHRASE,
                num_receive=1, num_change=1)
        print(f"\n=== mnemonic: {label} ===")
        print(f"    \"{mnemonic}\"")
        if passphrase:
            print("    passphrase: non-empty test passphrase")
        seen_recv0 = {}
        for ticker in coins:
            workspace = os.path.join(wsroot, ticker)
            m = multi[ticker]
            try:
                s = single_addresses(pybin, workspace, mnemonic, passphrase)
            except Exception as e:
                print(f"  {ticker:4} ERROR: {e}")
                overall_ok = False
                continue

            recv_ok = s["recv"] == m.receive
            change_ok = s["change"] == m.change
            ct_ok = s["coin_type"] == m.coin_type
            hrp_ok = s["hrp"] == m.hrp
            # Isolation: the electrum that derived this coin must come from THIS
            # coin's workspace, not a shared/site-packages one (rules out a default
            # electrum coincidentally producing the right answer).
            iso_ok = os.path.abspath(workspace) in s.get("electrum_file", "")
            kav_ok = True
            if label == "CANONICAL":
                kav_ok = m.receive[0] == KAV_RECEIVE0[ticker] == s["recv"][0]
            hrp_prefix_ok = all(a.startswith(f"{m.hrp}1") for a in s["recv"] + s["change"])
            wrong_pass_ok = True
            if wrong_multi is not None:
                wrong_pass_ok = (
                    wrong_multi[ticker].receive[0] != m.receive[0] and
                    wrong_multi[ticker].change[0] != m.change[0]
                )

            ok = (recv_ok and change_ok and ct_ok and hrp_ok and kav_ok and iso_ok
                  and hrp_prefix_ok and wrong_pass_ok)
            overall_ok = overall_ok and ok
            mark = "PASS" if ok else "FAIL"
            print(f"  {ticker:4} [{mark}] coin_type={s['coin_type']} hrp={s['hrp']} "
                  f"path={s['derivation']}")
            print(f"        single recv0={s['recv'][0]}")
            print(f"        multi  recv0={m.receive[0]}")
            print(f"        checked receive={len(s['recv'])} change={len(s['change'])}")
            if label == "CANONICAL":
                print(f"        kav    recv0={KAV_RECEIVE0[ticker]}")
            if wrong_multi is not None:
                print(f"        wrong-pass recv0={wrong_multi[ticker].receive[0]} "
                      f"different={wrong_pass_ok}")
            print(f"        electrum={s.get('electrum_file', '?')}  isolated={iso_ok}")
            if s["recv"][0] in seen_recv0:
                print(f"        CROSS-COIN FAIL: recv0 also used by {seen_recv0[s['recv'][0]]}")
                overall_ok = False
            seen_recv0[s["recv"][0]] = ticker
            if not ok:
                if not iso_ok:
                    print(f"        ISOLATION FAIL: electrum not from {workspace}")
                if not recv_ok:
                    print(f"        recv MISMATCH single={s['recv']} multi={m.receive}")
                if not change_ok:
                    print(f"        change MISMATCH single={s['change']} multi={m.change}")
                if not ct_ok:
                    print(f"        coin_type MISMATCH single={s['coin_type']} multi={m.coin_type}")
                if not hrp_ok:
                    print(f"        hrp MISMATCH single={s['hrp']} multi={m.hrp}")
                if not kav_ok:
                    print(f"        KAV MISMATCH")
                if not hrp_prefix_ok:
                    print("        HRP PREFIX FAIL: an address did not use the coin HRP")
                if not wrong_pass_ok:
                    print("        WRONG PASSPHRASE FAIL: wrong passphrase did not change addresses")

    print("\n" + ("PASS: single-coin wallets and the multiwallet derive identical "
                  "addresses for every coin and mnemonic." if overall_ok else
                  "FAIL: at least one coin/mnemonic mismatched (see above)."))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
