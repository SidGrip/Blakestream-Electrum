"""Regression check for the generalized (purpose/script_type) derivation.

Run on the .221 build venv:
    PYTHONPATH=/mnt/ram-build/electrium-multi2 .build-multi/venv/bin/python \
        unified/tests/test_user_coin_derivation.py

Guarantee: the six built-in coins derive BYTE-IDENTICALLY to before the refactor (golden zprv +
first receive address) — proving the generalization changed nothing for them.
"""

import sys

from unified import provisioning as p

M = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

# Captured from the pre-refactor code (the six built-ins must stay identical).
GOLDEN = {
    "BLC":  ("zprvAdhy2SjAuaziY7tMg5rP6DNLBQ4wegMMWbmpJ4bT5uwoNsFDXo63EyKnwTNUbuWv7Qry8ypnGu7adV8StmQmysr2ANeX4JrMLVcmDkP4iyr", "blc1ql27pg0ttv2pvdcqe06dw220epn5yxj64p89800"),
    "BBTC": ("zprvAdSYHPJVdorPFKQGvKavoKQhpCZ3hDdVxrt9EJ3f1jrUvnkGE1jHM1MTEva3ZoWBt1biqNFxBDCWNAtmae5MdbaFE8KtYTXRSkjBWH8HmMD", "bbtc1qlyg3wuu3zyw85ulz2my7wqh2rndtrufz2g4jdp"),
    "ELT":  ("zprvAe4eETE4Nswwo7qYaXRPys5uU3Y1BqX2xSoDE5cTHW8CYRTUMzNfox5pbFKeXH8idSjXvyA6CJGqTVS7oRXy2VvQkuN1KNSKr1bHsFARopC", "elt1qjammm4pj40cvmqhwpj3jj0pcj0w3r2ds9z943m"),
    "LIT":  ("zprvAczWxixeHZQnwi6pEcWC7DTAXquPH7HNFE3DV6PafJJAhe3uFhpkiSmibZHLVEjxhjtRdUufJ9pRWwdq6RdJnnXYdDj1nE2DQphNfU7Tdry", "lit1qv9e6sqrlvuxge76s95lzy549034ewmpum9rlv9"),
    "PHO":  ("zprvAckVCqaUYoPBhiqeWqUKFVqG2QS8RbSbPH1Q8hGnKhDh2E57AYKn1m5kkvRgqoxpdMfaL7KreZ8WB2KqaCjF7Za4sM4gijLaifSqCtGmnZ4", "pho1q4l6rh9wedm5w7jz2ph9s3nwtxuh9zl8hkv2mf8"),
    "UMO":  ("zprvAcNQ62YYKEnrR4jZFUM5X7mWfrZ9AMe5daCVujYHuNxLGn354Je3ZRQK3gPZGzMBQDLFTvjtcdf6ZhXtBox3fgyG751qootTUjskmJf2bDy", "umo1qnk09lsphnthwcyhr6km63ug5vzhweh9aksgup3"),
}


def main():
    fails = []

    coins = p.load_coins()
    root = p._root_from_mnemonic(M)
    for t, (g_xprv, g_recv) in GOLDEN.items():
        if t not in coins:
            fails.append(f"{t}: not in coins.json"); continue
        xprv = p.derive_account_xprv(M, ticker=t, coins=coins)
        recv = p.derive_coin(root, coins[t]).receive[0]
        if xprv != g_xprv:
            fails.append(f"{t}: account xprv changed!\n   got {xprv}\n   exp {g_xprv}")
        if recv != g_recv:
            fails.append(f"{t}: recv[0] changed! got {recv} exp {g_recv}")
    print(f"built-ins byte-identical: {'OK' if not fails else 'FAIL'}")

    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nALL DERIVATION CHECKS PASSED")


if __name__ == "__main__":
    main()
