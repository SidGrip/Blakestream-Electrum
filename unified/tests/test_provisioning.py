"""P1 tests: one mnemonic -> six correct coin addresses; mining-key import."""

import pytest

from unified import provisioning as P

# Standard BIP39 test mnemonic.
ABANDON = ("abandon abandon abandon abandon abandon abandon "
           "abandon abandon abandon abandon abandon about")

# Known-answer (verified against the fork's own primitives during the build):
# receive[0] = m/84'/<coin_type>'/0'/0/0 (BIP84 native segwit) for the ABANDON mnemonic.
# BLC matches a standalone Electrum BIP39 native-segwit restore (same seed = same wallet).
KAV_RECEIVE0 = {
    "BLC": "blc1ql27pg0ttv2pvdcqe06dw220epn5yxj64p89800",
    "BBTC": "bbtc1qlyg3wuu3zyw85ulz2my7wqh2rndtrufz2g4jdp",
    "ELT": "elt1qjammm4pj40cvmqhwpj3jj0pcj0w3r2ds9z943m",
    "LIT": "lit1qv9e6sqrlvuxge76s95lzy549034ewmpum9rlv9",
    "PHO": "pho1q4l6rh9wedm5w7jz2ph9s3nwtxuh9zl8hkv2mf8",
    "UMO": "umo1qnk09lsphnthwcyhr6km63ug5vzhweh9aksgup3",
}

# eloipool Go test vector (merged-mine-proxy-go/internal/miningkey/miningkey_test.go):
# HASH160 -> testnet P2WPKH under each chain HRP.
GO_MINING_KEY = "a5d3e00343efe51e81d39884a74124ca060fefdd"
GO_VECTORS = {
    "tbbtc": "tbbtc1q5hf7qq6ralj3aqwnnzz2wsfyegrqlm7afa890n",
    "tlit": "tlit1q5hf7qq6ralj3aqwnnzz2wsfyegrqlm7augmrw0",
}

PRIV = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def test_one_seed_six_distinct_addresses():
    coins = P.load_coins()
    sets = P.derive_all(ABANDON)
    assert set(sets) == set(coins)
    addrs = [s.receive[0] for s in sets.values()]
    assert len(set(addrs)) == 6, "each coin must yield a distinct address"
    for ticker, s in sets.items():
        assert s.receive[0].startswith(coins[ticker]["segwit_hrp"] + "1")
        assert s.coin_type == coins[ticker]["coin_type"]


def test_known_answer_vectors():
    sets = P.derive_all(ABANDON)
    for ticker, expected in KAV_RECEIVE0.items():
        assert sets[ticker].receive[0] == expected


def test_mining_key_watchonly_matches_pool():
    for hrp, expected in GO_VECTORS.items():
        assert P.address_from_mining_key(GO_MINING_KEY, hrp) == expected


def test_mining_key_spend_equals_watchonly():
    h160 = P.mining_key_hash160(PRIV)
    for hrp in ("blc", "bbtc", "pho"):
        assert P.address_from_privkey(PRIV, hrp) == P.address_from_mining_key(h160, hrp)


def test_mining_key_addresses_all_inputs_agree():
    pub = P._pubkey_from_privkey(bytes.fromhex(PRIV)).hex()
    by_priv = P.mining_key_addresses(privkey_hex=PRIV)
    by_h160 = P.mining_key_addresses(mining_key_hex=P.mining_key_hash160(PRIV))
    by_pub = P.mining_key_addresses(pubkey_hex=pub)
    assert by_priv == by_h160 == by_pub
    assert len(set(by_priv.values())) == 6


def test_mining_key_addresses_requires_exactly_one_input():
    with pytest.raises(ValueError):
        P.mining_key_addresses()
    with pytest.raises(ValueError):
        P.mining_key_addresses(privkey_hex=PRIV, mining_key_hex=P.mining_key_hash160(PRIV))


def test_wif_roundtrip():
    from electrum.bitcoin import deserialize_privkey
    imp = P.mining_key_import_string(PRIV)
    assert imp.startswith("p2wpkh:")
    txin_type, secret, compressed = deserialize_privkey(imp)
    assert txin_type == "p2wpkh"
    assert secret.hex() == PRIV
    assert compressed is True


def test_invalid_bip39_rejected():
    bad = " ".join(["abandon"] * 12)  # wrong checksum word
    assert not P.is_valid_bip39(bad)
    with pytest.raises(ValueError):
        P.derive_all(bad)


def test_passphrase_changes_addresses():
    a = P.derive_all(ABANDON)["BLC"].receive[0]
    b = P.derive_all(ABANDON, passphrase="trezor")["BLC"].receive[0]
    assert a != b


def test_account_and_index_increments_distinct():
    a0 = P.derive_all(ABANDON, account=0)["BLC"].receive[0]
    a1 = P.derive_all(ABANDON, account=1)["BLC"].receive[0]
    multi = P.derive_all(ABANDON, num_receive=3, num_change=2)["BLC"]
    assert a0 != a1
    assert len(set(multi.receive)) == 3
    assert len(set(multi.change)) == 2
    assert not (set(multi.receive) & set(multi.change))


def test_testnet_hrp_and_coin_type():
    coins = P.load_coins()
    sets = P.derive_all(ABANDON, net="testnet")
    for ticker, s in sets.items():
        assert s.receive[0].startswith(coins[ticker]["testnet_segwit_hrp"] + "1")
        assert s.coin_type == P.TESTNET_COIN_TYPE


def test_invalid_key_inputs_rejected():
    with pytest.raises(ValueError):
        P.address_from_privkey("00" * 31, "blc")          # 31-byte privkey
    with pytest.raises(ValueError):
        P.address_from_mining_key("00" * 19, "blc")        # 19-byte HASH160
    with pytest.raises(ValueError):
        P.address_from_pubkey("04" + "00" * 64, "blc")     # 65-byte (uncompressed)
    with pytest.raises(ValueError):
        P.mining_key_wif("00" * 31)                        # bad privkey length


def test_provision_for_daemon():
    info = P.provision_for_daemon("BLC", ABANDON)
    assert info["coin_type"] == 10
    assert info["account_path"] == "m/84'/10'/0'"
    assert info["receive_0"] == KAV_RECEIVE0["BLC"]
    assert info["hrp"] == "blc"
    assert info["change_0"].startswith("blc1")
    assert info["change_0"] != info["receive_0"]


def test_mining_key_import_for_coin():
    coins = P.load_coins()
    a = P.mining_key_import_string(PRIV)
    b = P.mining_key_import_for_coin(PRIV, coins["UMO"])
    assert a == b  # all six share WIF byte 0x80 today


def test_account_zprv_reproduces_daemon_address():
    # Approach A: hand the daemon the account zprv; it derives [change/index] below.
    from electrum import crypto, segwit_addr
    from electrum.bip32 import BIP32Node
    zprv = P.derive_account_xprv(ABANDON, ticker="BLC")
    assert zprv.startswith("zprv")
    acct = BIP32Node.from_xkey(zprv)
    assert acct.xtype == "p2wpkh" and acct.is_private()
    child = acct.subkey_at_private_derivation([0, 0])  # change=0, index=0
    pub = child.eckey.get_public_key_bytes(compressed=True)
    addr = segwit_addr.encode_segwit_address("blc", 0, crypto.hash_160(pub))
    assert addr == KAV_RECEIVE0["BLC"], "account-zprv restore must match the seed-derived address"


def test_generate_mnemonic_valid():
    for n in (12, 24):
        m = P.generate_mnemonic(n)
        assert len(m.split()) == n
        assert P.is_valid_bip39(m)
