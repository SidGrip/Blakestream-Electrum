#!/usr/bin/env python3
"""Repeatable LIVE-SEND QA against a running, unlocked, funded multiwallet backend.

Exercises the real fund-moving path end to end through the Tools routes — build a payment with
pay-to-many, broadcast it, then confirm the network accepted it with fetch-tx — using a small
SELF-SEND (to a fresh wallet address) so only the miner fee leaves the wallet and the balance
lasts for many runs.

Point it at an already-running backend (e.g. the headless ``electrum-backend … multi --serve`` or
the desktop app) and give it that launch's bearer token:

    QA_TOKEN=<token> .venv/bin/python -m unified.tests.live_send_qa
    QA_TOKEN=<token> QA_LIVE_COIN=PHO QA_LIVE_AMOUNT=2 QA_LIVE_FEERATE=1 \
        .venv/bin/python -m unified.tests.live_send_qa

Env:
  QA_API        backend base URL          (default http://127.0.0.1:57100)
  QA_TOKEN      bearer token for that launch (REQUIRED)
  QA_LIVE_COIN  coin to test              (default BLC)
  QA_LIVE_AMOUNT amount to self-send      (default 1) — keep it small so the balance lasts
  QA_LIVE_FEERATE sat/vByte               (default 1) — required: these chains expose no dynamic fee est.
  QA_NO_BROADCAST set to 1 to BUILD + verify only, without broadcasting (dry run)

Exit 0 = the send built, broadcast, and was found on the network; 1 = a step failed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("QA_API", "http://127.0.0.1:57100")
TOKEN = os.environ.get("QA_TOKEN")
COIN = os.environ.get("QA_LIVE_COIN", "BLC").upper()
AMOUNT = os.environ.get("QA_LIVE_AMOUNT", "1")
FEERATE = os.environ.get("QA_LIVE_FEERATE", "1")
DRY = os.environ.get("QA_NO_BROADCAST") == "1"


def call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Host": "127.0.0.1", "Authorization": "Bearer " + (TOKEN or ""),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


def coin_amount(coin):
    s, r = call("GET", "/portfolio", timeout=20)
    if s != 200:
        return None
    return (r.get("coins", {}).get(coin) or {}).get("amount")


def main():
    if not TOKEN:
        print("ERROR: QA_TOKEN is required (the running backend's per-launch bearer token).")
        return 2
    print(f"=== live-send QA  coin={COIN} amount={AMOUNT} feerate={FEERATE} dry_run={DRY} api={API} ===")

    s, r = call("GET", "/health", timeout=10)
    if s != 200:
        print(f"ERROR: backend not reachable/authorized at {API} (status {s}: {r}).")
        return 1

    before = coin_amount(COIN)
    print(f"balance before: {before} {COIN}")
    if before is None:
        print(f"ERROR: no balance for {COIN} (is it unlocked + synced?).")
        return 1
    if float(before) <= float(AMOUNT):
        print(f"ERROR: balance {before} too low to self-send {AMOUNT} + fee.")
        return 1

    # 1) fresh receive address (self-send keeps the funds in the wallet)
    s, r = call("POST", f"/receive/{COIN}/new", {})
    addr = r.get("address") if s == 200 else None
    if not addr:
        print(f"ERROR: could not get a receive address ({s}: {r}).")
        return 1
    print(f"self-send to: {addr}")

    # 2) build via Tools pay-to-many
    s, built = call("POST", f"/tools/{COIN}/pay-to-many",
                    {"outputs": [[addr, str(AMOUNT)]], "feerate": str(FEERATE)}, timeout=90)
    if s != 200 or "error" in built:
        print(f"FAIL: pay-to-many build failed ({s}): {built.get('error', built)}")
        return 1
    raw = built.get("raw")
    txid = built.get("txid")
    # fee ~= (balance in) - (total outputs) for a self-send that spends a single round balance
    fee_sats = int(round(float(before) * 1e8)) - int(built.get("total_out_sats") or 0)
    print(f"built: txid={txid} size={built.get('size')} complete={built.get('complete')} "
          f"outputs={len(built.get('outputs') or [])} approx_fee_sats~{fee_sats if fee_sats > 0 else '?'}")
    if not raw or built.get("complete") is False:
        print("FAIL: built tx is empty or incomplete.")
        return 1

    if DRY:
        print("DRY RUN: built + signed OK; not broadcasting. (set QA_NO_BROADCAST=0 to send for real)")
        return 0

    # 3) broadcast
    s, br = call("POST", f"/tools/{COIN}/broadcast", {"tx": raw}, timeout=60)
    if s != 200 or "error" in br:
        print(f"FAIL: broadcast failed ({s}): {br.get('error', br)}")
        return 1
    sent_txid = br.get("txid")
    print(f"BROADCAST ok: {sent_txid}")

    # 4) confirm the network has it (fetch-tx -> gettransaction)
    found = False
    for _ in range(6):
        time.sleep(2)
        s, f = call("POST", f"/tools/{COIN}/fetch-tx", {"txid": sent_txid}, timeout=30)
        if s == 200 and f.get("txid") == sent_txid:
            found = True
            break
    print(f"network has the tx (fetch-tx): {found}")

    time.sleep(2)
    after = coin_amount(COIN)
    print(f"balance after:  {after} {COIN}  (delta = {float(after) - float(before):.8f}, ~the fee for a self-send)")

    ok = found and after is not None and float(after) <= float(before)
    print("=== LIVE-SEND " + ("PASSED" if ok else "FAILED") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
