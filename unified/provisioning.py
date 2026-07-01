"""Net-free single-seed provisioning for the unified Blakestream Electrum wallet.

One BIP39 mnemonic derives keys/addresses for all six Blakestream coins via
per-coin BIP84 ``m/84'/<coin_type>'/<account>'/<change>/<index>`` (native segwit,
p2wpkh — the standard path so the SAME seed restores the SAME wallet in a standalone
single-coin Electrum and in hardware wallets); coins differ
only by their bech32 HRP (and other prefixes) at address-encoding time. A single
"mining key" (the eloipool private key) can also be imported and yields the same
P2WPKH address the pool pays out to, on every chain.

This module is deliberately NET-FREE: it never calls
``electrum.constants.set_as_network`` and never reads ``constants.net``. All
per-coin parameters come from ``coin-overlays/coins.json``. The running per-coin
daemons still set their own network at runtime for signing/keystore work; the
separation is intentional (see ``blakestream-electrum.md`` §4/§5).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import electrum_ecc as ecc
from electrum import crypto, keystore, segwit_addr
from electrum.bip32 import BIP32Node
from electrum.bitcoin import EncodeBase58Check, EncodeBase58CheckBlake
from electrum.mnemonic import Wordlist

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_coins_json() -> str:
    # PyInstaller bundles coins.json under coin-overlays/ at the bundle root.
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", REPO_ROOT), "coin-overlays", "coins.json")
    return os.path.join(REPO_ROOT, "coin-overlays", "coins.json")


COINS_JSON = _default_coins_json()

WITNESS_V0 = 0
TESTNET_COIN_TYPE = 1  # conventional BIP44 testnet coin_type
_STRENGTH_BITS = {12: 128, 15: 160, 18: 192, 21: 224, 24: 256}


# --------------------------------------------------------------------------- #
# coin metadata
# --------------------------------------------------------------------------- #

def load_coins(path: Optional[str] = None) -> Dict[str, dict]:
    with open(path or _default_coins_json(), encoding="utf-8") as f:
        return json.load(f)


def _hrp(coin: dict, net: str) -> str:
    return coin["segwit_hrp"] if net == "mainnet" else coin["testnet_segwit_hrp"]


def _wif_prefix(coin: dict, net: str) -> int:
    return coin["wif_prefix"] if net == "mainnet" else coin["testnet_wif_prefix"]


def _coin_type(coin: dict, net: str) -> int:
    return coin["coin_type"] if net == "mainnet" else TESTNET_COIN_TYPE


# A coin's script_type drives BOTH its BIP44 purpose AND its SLIP-132 xtype (the master-key
# prefix Electrum infers the script type from). These three always move together. Absent
# (the 6 built-ins) it defaults to native segwit, so their derivation is byte-identical.
_SCRIPT = {
    "p2pkh":       (44, "standard"),     # legacy  -> xprv  (1…)
    "p2wpkh-p2sh": (49, "p2wpkh-p2sh"),  # wrapped -> yprv  (3…)
    "p2wpkh":      (84, "p2wpkh"),       # native segwit -> zprv (the six built-ins)
}


def _script_type(coin: dict) -> str:
    st = coin.get("script_type") or "p2wpkh"
    if st not in _SCRIPT:
        raise ValueError(f"unsupported script_type {st!r}")
    return st


def _purpose(coin: dict) -> int:
    return _SCRIPT[_script_type(coin)][0]


def _xtype(coin: dict) -> str:
    return _SCRIPT[_script_type(coin)][1]


def _xprv_net(net: str):
    # Mainnet -> None so to_xprv falls back to constants.net (BitcoinMainnet), keeping the 6
    # byte-identical. Testnet/regtest user coins need testnet SLIP-132 prefixes (tprv/uprv/vprv).
    if net == "mainnet":
        return None
    from electrum import constants
    return constants.BitcoinRegtest if net == "regtest" else constants.BitcoinTestnet


def _p2pkh_prefix(coin: dict, net: str) -> int:
    if net == "mainnet":
        return coin["p2pkh"]
    if net == "regtest":
        return coin.get("regtest_p2pkh", coin["testnet_p2pkh"])
    return coin["testnet_p2pkh"]


def _p2sh_prefix(coin: dict, net: str) -> int:
    if net == "mainnet":
        return coin["p2sh"]
    if net == "regtest":
        return coin.get("regtest_p2sh", coin["testnet_p2sh"])
    return coin["testnet_p2sh"]


# --------------------------------------------------------------------------- #
# BIP39 mnemonic (generation + validation), net-free, using Electrum's wordlist
# --------------------------------------------------------------------------- #

_WORDLIST: Optional[Wordlist] = None


def _wordlist() -> Wordlist:
    global _WORDLIST
    if _WORDLIST is None:
        _WORDLIST = Wordlist.from_file("english.txt")
    return _WORDLIST


def entropy_to_mnemonic(entropy: bytes) -> str:
    if len(entropy) not in (16, 20, 24, 28, 32):
        raise ValueError("entropy must be 128..256 bits in 32-bit steps")
    wl = _wordlist()
    ent_bits = "".join(f"{b:08b}" for b in entropy)
    cs_len = len(entropy) * 8 // 32
    cs_bits = "".join(f"{b:08b}" for b in hashlib.sha256(entropy).digest())[:cs_len]
    bits = ent_bits + cs_bits
    return " ".join(wl[int(bits[i:i + 11], 2)] for i in range(0, len(bits), 11))


def generate_mnemonic(num_words: int = 12) -> str:
    if num_words not in _STRENGTH_BITS:
        raise ValueError(f"num_words must be one of {sorted(_STRENGTH_BITS)}")
    mnemonic = entropy_to_mnemonic(os.urandom(_STRENGTH_BITS[num_words] // 8))
    assert is_valid_bip39(mnemonic), "internally generated mnemonic failed checksum"
    return mnemonic


def is_valid_bip39(mnemonic: str) -> bool:
    is_checksum_valid, is_wordlist_valid = keystore.bip39_is_checksum_valid(mnemonic)
    return bool(is_checksum_valid and is_wordlist_valid)


# --------------------------------------------------------------------------- #
# address encoding (net-free)
# --------------------------------------------------------------------------- #

def _p2wpkh_from_pubkey(pubkey: bytes, hrp: str) -> str:
    return segwit_addr.encode_segwit_address(hrp, WITNESS_V0, crypto.hash_160(pubkey))


def _p2pkh_from_pubkey(pubkey: bytes, coin: dict, net: str) -> str:
    # Standard (double-SHA256) base58check P2PKH, as legacy Bitcoin-fork coins use.
    # The six Blakestream coins are native-segwit/bech32 and never reach this path.
    return EncodeBase58Check(bytes([_p2pkh_prefix(coin, net)]) + crypto.hash_160(pubkey))


def _p2sh_p2wpkh_from_pubkey(pubkey: bytes, coin: dict, net: str) -> str:
    # Wrapped segwit: P2SH of the P2WPKH redeem script OP_0 <hash160(pubkey)>.
    redeem = b"\x00\x14" + crypto.hash_160(pubkey)
    return EncodeBase58Check(bytes([_p2sh_prefix(coin, net)]) + crypto.hash_160(redeem))


def _pubkey_from_privkey(privkey: bytes) -> bytes:
    if len(privkey) != 32:
        raise ValueError("private key must be exactly 32 bytes")
    return ecc.ECPrivkey(privkey).get_public_key_bytes(compressed=True)


# --------------------------------------------------------------------------- #
# HD derivation from one mnemonic
# --------------------------------------------------------------------------- #

@dataclass
class CoinAddressSet:
    ticker: str
    coin_type: int
    hrp: str
    receive: List[str]
    change: List[str]
    script_type: str = "p2wpkh"


def _root_from_mnemonic(mnemonic: str, passphrase: str = "") -> BIP32Node:
    if not is_valid_bip39(mnemonic):
        raise ValueError("invalid BIP39 mnemonic (bad checksum or unknown word)")
    seed = keystore.bip39_to_seed(mnemonic, passphrase=passphrase)
    return BIP32Node.from_rootseed(seed, xtype="standard")


def derive_coin(
    root: BIP32Node,
    coin: dict,
    *,
    net: str = "mainnet",
    account: int = 0,
    num_receive: int = 1,
    num_change: int = 1,
) -> CoinAddressSet:
    coin_type = _coin_type(coin, net)
    purpose = _purpose(coin)
    script_type = _script_type(coin)
    hrp = _hrp(coin, net) if script_type == "p2wpkh" else ""

    def address(change: int, index: int) -> str:
        path = f"m/{purpose}'/{coin_type}'/{account}'/{change}/{index}"
        node = root.subkey_at_private_derivation(path)
        pub = node.eckey.get_public_key_bytes(compressed=True)
        if script_type == "p2pkh":
            return _p2pkh_from_pubkey(pub, coin, net)
        if script_type == "p2wpkh-p2sh":
            return _p2sh_p2wpkh_from_pubkey(pub, coin, net)
        return _p2wpkh_from_pubkey(pub, hrp)

    return CoinAddressSet(
        ticker=coin["ticker"],
        coin_type=coin_type,
        hrp=hrp,
        receive=[address(0, i) for i in range(num_receive)],
        change=[address(1, i) for i in range(num_change)],
        script_type=script_type,
    )


def derive_all(
    mnemonic: str,
    passphrase: str = "",
    *,
    net: str = "mainnet",
    account: int = 0,
    num_receive: int = 1,
    num_change: int = 1,
    coins: Optional[Dict[str, dict]] = None,
) -> Dict[str, CoinAddressSet]:
    """One mnemonic -> per-coin address sets for every coin in coins.json."""
    coins = coins if coins is not None else load_coins()
    root = _root_from_mnemonic(mnemonic, passphrase)
    return {
        ticker: derive_coin(root, coin, net=net, account=account,
                            num_receive=num_receive, num_change=num_change)
        for ticker, coin in coins.items()
    }


# --------------------------------------------------------------------------- #
# helpers the daemon orchestrator (P2) needs
# --------------------------------------------------------------------------- #

def account_derivation_path(coin_type: int, account: int = 0, purpose: int = 84) -> str:
    return f"m/{purpose}'/{coin_type}'/{account}'"


def address_derivation_path(coin_type: int, account: int = 0, change: int = 0, index: int = 0, purpose: int = 84) -> str:
    return f"m/{purpose}'/{coin_type}'/{account}'/{change}/{index}"


def provision_for_daemon(
    ticker: str,
    mnemonic: str,
    passphrase: str = "",
    *,
    net: str = "mainnet",
    account: int = 0,
    coins: Optional[Dict[str, dict]] = None,
) -> dict:
    """Everything the orchestrator needs to restore one coin's wallet from the
    shared mnemonic: the explicit BIP84 account path to hand the daemon, plus the
    first receive/change addresses to verify the restore landed correctly."""
    coins = coins if coins is not None else load_coins()
    coin = coins[ticker]
    aset = derive_coin(_root_from_mnemonic(mnemonic, passphrase), coin, net=net, account=account)
    return {
        "ticker": ticker,
        "coin_type": aset.coin_type,
        "hrp": aset.hrp,
        "script_type": aset.script_type,
        "account_path": account_derivation_path(aset.coin_type, account, _purpose(coin)),
        "receive_0": aset.receive[0],
        "change_0": aset.change[0],
    }


def derive_account_xprv(
    mnemonic: str,
    passphrase: str = "",
    *,
    ticker: str,
    account: int = 0,
    net: str = "mainnet",
    coins: Optional[Dict[str, dict]] = None,
) -> str:
    """SLIP-132 account extended key for ``m/<purpose>'/<coin_type>'/<account>'`` —
    ready for ``electrum daemon restore <xkey>``. The prefix encodes the script type:
    ``zprv`` for native segwit (the six built-ins, purpose 84'), ``yprv`` for wrapped
    segwit (49'), ``xprv`` for legacy (44', e.g. Dogecoin); testnet emits tprv/uprv/vprv.

    This is the P2 provisioning primitive (approach A): the mnemonic never leaves
    the orchestrator; only this per-coin account xprv is handed to each daemon,
    which derives the receive/change chains below it.
    """
    coins = coins if coins is not None else load_coins()
    coin = coins[ticker]
    coin_type = _coin_type(coin, net)
    root = _root_from_mnemonic(mnemonic, passphrase)
    node = root.subkey_at_private_derivation(account_derivation_path(coin_type, account, _purpose(coin)))
    return node._replace(xtype=_xtype(coin)).to_xprv(net=_xprv_net(net))


# --------------------------------------------------------------------------- #
# mining-key import (single private key -> per-coin P2WPKH), eloipool-compatible
# --------------------------------------------------------------------------- #

def mining_key_wif(privkey_hex: str, wif_prefix: int = 0x80) -> str:
    """32-byte hex private key -> compressed WIF for the given network byte."""
    priv = bytes.fromhex(privkey_hex)
    if len(priv) != 32:
        raise ValueError("private key must be exactly 32 bytes")
    # Blakestream coins use a blake256 base58check checksum (not double-SHA256).
    return EncodeBase58CheckBlake(bytes([wif_prefix]) + priv + b"\x01")


def mining_key_import_string(privkey_hex: str, wif_prefix: int = 0x80) -> str:
    """Electrum imported-keystore string for a native-SegWit (P2WPKH) key."""
    return "p2wpkh:" + mining_key_wif(privkey_hex, wif_prefix)


def mining_key_import_for_coin(privkey_hex: str, coin: dict, *, net: str = "mainnet") -> str:
    """Per-coin import string (uses that coin's WIF prefix; all six are 0x80 today
    but this stays correct if a coin ever diverges)."""
    return mining_key_import_string(privkey_hex, _wif_prefix(coin, net))


def mining_key_hash160(privkey_hex: str) -> str:
    """HASH160(pubkey) == the eloipool 'mining key' / stratum username (40 hex)."""
    return crypto.hash_160(_pubkey_from_privkey(bytes.fromhex(privkey_hex))).hex()


def address_from_privkey(privkey_hex: str, hrp: str) -> str:
    return _p2wpkh_from_pubkey(_pubkey_from_privkey(bytes.fromhex(privkey_hex)), hrp)


def address_from_pubkey(pubkey_hex: str, hrp: str) -> str:
    pub = bytes.fromhex(pubkey_hex)
    if len(pub) != 33 or pub[0] not in (0x02, 0x03):
        raise ValueError("expected 33-byte compressed secp256k1 pubkey")
    return _p2wpkh_from_pubkey(pub, hrp)


def address_from_mining_key(mining_key_hex: str, hrp: str) -> str:
    """Watch-only: the bare 40-hex HASH160 -> P2WPKH address (matches the pool's
    AddressFromV2MiningKey)."""
    h160 = bytes.fromhex(mining_key_hex)
    if len(h160) != 20:
        raise ValueError("mining key (HASH160) must be exactly 20 bytes (40 hex)")
    return segwit_addr.encode_segwit_address(hrp, WITNESS_V0, h160)


def mining_key_addresses(
    *,
    privkey_hex: Optional[str] = None,
    pubkey_hex: Optional[str] = None,
    mining_key_hex: Optional[str] = None,
    net: str = "mainnet",
    coins: Optional[Dict[str, dict]] = None,
) -> Dict[str, str]:
    """Per-coin P2WPKH address from a mining key, supplied as a spendable private
    key, a compressed pubkey, or the bare HASH160 (both pubkey/HASH160 are
    watch-only)."""
    if sum(x is not None for x in (privkey_hex, pubkey_hex, mining_key_hex)) != 1:
        raise ValueError("supply exactly one of privkey_hex, pubkey_hex, mining_key_hex")
    coins = coins if coins is not None else load_coins()
    if privkey_hex is not None:
        h160_hex = mining_key_hash160(privkey_hex)
    elif pubkey_hex is not None:
        pub = bytes.fromhex(pubkey_hex)
        if len(pub) != 33 or pub[0] not in (0x02, 0x03):
            raise ValueError("expected 33-byte compressed secp256k1 pubkey")
        h160_hex = crypto.hash_160(pub).hex()
    else:
        h160_hex = mining_key_hex
    return {t: address_from_mining_key(h160_hex, _hrp(c, net)) for t, c in coins.items()}
