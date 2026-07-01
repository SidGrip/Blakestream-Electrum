"""Sweep-based migration: move funds from a user's LEGACY per-coin wallet into the
unified single-seed wallet.

Why a sweep (and not importing the old keys): importing legacy keys into the unified
wallet would put non-derivable keys behind the one master seed, so a future
seed-only restore would silently lose them. A sweep moves every UTXO ON-CHAIN to an
address the unified seed CAN re-derive, keeping the "one seed restores everything"
guarantee intact.

The fund-loss-critical parts and how they're handled:

  * **Exhaustive discovery.** A field wallet could hold funds under several
    derivation schemes — Electrum-native seeds (fixed ``m/`` / ``m/0'`` paths), BIP39
    at the inherited ``m/44'/10'/…`` (all six legacy coins shared coin_type 10) or at
    the coin's own coin_type, across purposes 44/49/84, plus bare imported WIFs.
    :func:`legacy_schemes` enumerates them; missing one strands funds, so unsupported
    shapes (old non-BIP32 mpk seeds, multisig, SLIP39) are WARNED about, never
    silently skipped.
  * **The daemon does all the crypto.** Discovery is net-free here; the actual sweep
    runs through the online per-coin daemon's ``sweep`` command (Blakestream's
    single-SHA256 txid / double-SHA256 BIP143 sighash / blake256 checksums live
    there). This module never touches raw transaction bytes.
  * **The sweep IS the dry-run.** ``sweep`` discovers UTXOs and returns a signed tx
    WITHOUT broadcasting, so the previewed amount is exactly what gets broadcast.
    Nothing hits the chain until the user confirms and :meth:`Migrator.execute` runs.
  * **No false "done".** Success requires the broadcast tx to confirm AND the scanned
    source addresses to read empty — never broadcast alone (RBF / mempool eviction).
  * **No cross-chain send.** The destination must be a unified address of the SAME
    coin (HRP-checked); a BLC sweep can't be aimed at a ``umo1`` address.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import electrum_ecc as ecc
from electrum import crypto, keystore, segwit_addr
from electrum.bip32 import BIP32Node
from electrum.bitcoin import EncodeBase58CheckBlake
from electrum.mnemonic import Mnemonic, is_new_seed
from electrum.transaction import Transaction

from unified import provisioning

WITNESS_V0 = 0
INHERITED_COIN_TYPE = 10   # all six legacy wallets shared Blakecoin's coin_type
DEFAULT_GAP = 20           # consecutive-unused stop, standard Electrum gap limit
SWEEP_IMAX = 100           # the daemon's sweep input cap (commands.sweep imax default)


# --------------------------------------------------------------------------- #
# net-aware coin prefixes (mainnet / testnet / regtest), self-contained so this
# module owns its own net handling and never sets a process-global network.
# --------------------------------------------------------------------------- #

def _net_key(coin: dict, net: str, key: str):
    if net == "mainnet":
        return coin[key]
    return coin[f"{net}_{key}"]   # testnet_<key> / regtest_<key>


def _hrp(coin: dict, net: str) -> str:
    return _net_key(coin, net, "segwit_hrp")


def _wif_prefix(coin: dict, net: str) -> int:
    return _net_key(coin, net, "wif_prefix")


def _p2pkh_version(coin: dict, net: str) -> int:
    return _net_key(coin, net, "p2pkh")


def _p2sh_version(coin: dict, net: str) -> int:
    return _net_key(coin, net, "p2sh")


def _coin_type(coin: dict, net: str) -> int:
    return coin["coin_type"] if net == "mainnet" else provisioning.TESTNET_COIN_TYPE


# --------------------------------------------------------------------------- #
# the candidate derivation schemes a legacy field wallet could hold
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Scheme:
    label: str
    root_path: str       # e.g. "m", "m/0'", "m/44'/10'/0'"
    addr_type: str       # 'p2pkh' | 'p2wpkh' | 'p2wpkh-p2sh'
    seed_kind: str       # 'electrum' (native seed) | 'bip39'


def legacy_schemes(coin: dict, net: str = "mainnet") -> List[Scheme]:
    """Every derivation scheme a legacy wallet for ``coin`` could realistically use.
    Order is best-effort most-likely first; all are scanned regardless."""
    schemes: List[Scheme] = [
        Scheme("electrum-standard", "m",    "p2pkh",  "electrum"),
        Scheme("electrum-segwit",   "m/0'", "p2wpkh", "electrum"),
    ]
    own_ct = _coin_type(coin, net)
    # Inherited coin_type 10 first (the live legacy path for all six coins), then the
    # coin's own coin_type if different (a user may have set it manually).
    cts: List[int] = [INHERITED_COIN_TYPE]
    if own_ct != INHERITED_COIN_TYPE:
        cts.append(own_ct)
    for ct in cts:
        schemes += [
            Scheme(f"bip44 m/44'/{ct}'", f"m/44'/{ct}'/0'", "p2pkh",       "bip39"),
            Scheme(f"bip49 m/49'/{ct}'", f"m/49'/{ct}'/0'", "p2wpkh-p2sh", "bip39"),
            Scheme(f"bip84 m/84'/{ct}'", f"m/84'/{ct}'/0'", "p2wpkh",      "bip39"),
        ]
    return schemes


# --------------------------------------------------------------------------- #
# net-free key/address derivation (the part proved against known-answer vectors)
# --------------------------------------------------------------------------- #

def _bip39_root(words: str, passphrase: str = "") -> BIP32Node:
    seed = keystore.bip39_to_seed(words, passphrase=passphrase)
    return BIP32Node.from_rootseed(seed, xtype="standard")


def _electrum_root(words: str, passphrase: str = "") -> BIP32Node:
    # Electrum-native seeds derive their BIP32 seed differently from BIP39.
    seed = Mnemonic.mnemonic_to_seed(words, passphrase=passphrase)
    return BIP32Node.from_rootseed(seed, xtype="standard")


def detect_seed_kinds(words: str) -> List[str]:
    """Which seed interpretations a phrase satisfies. A phrase can be valid as BIP39,
    as an Electrum-native seed, or (rarely) both — scan every kind it matches so funds
    under either interpretation are found."""
    kinds: List[str] = []
    if provisioning.is_valid_bip39(words):
        kinds.append("bip39")
    if is_new_seed(words):
        kinds.append("electrum")
    return kinds


def _address_for(pub: bytes, addr_type: str, coin: dict, net: str) -> str:
    h160 = crypto.hash_160(pub)
    if addr_type == "p2wpkh":
        return segwit_addr.encode_segwit_address(_hrp(coin, net), WITNESS_V0, h160)
    if addr_type == "p2pkh":
        return EncodeBase58CheckBlake(bytes([_p2pkh_version(coin, net)]) + h160)
    if addr_type == "p2wpkh-p2sh":
        redeem = b"\x00\x14" + h160                       # witness v0 keyhash program
        return EncodeBase58CheckBlake(bytes([_p2sh_version(coin, net)]) + crypto.hash_160(redeem))
    raise ValueError(f"unknown addr_type {addr_type!r}")


def _wif(priv32: bytes, coin: dict, net: str) -> str:
    # Compressed WIF with the coin's network byte and blake256 base58check checksum.
    return EncodeBase58CheckBlake(bytes([_wif_prefix(coin, net)]) + priv32 + b"\x01")


@dataclass
class DerivedKey:
    scheme: str
    path: str
    address: str
    wif: str


def derive_at(root: BIP32Node, scheme: Scheme, change: int, index: int,
              coin: dict, net: str) -> DerivedKey:
    path = f"{scheme.root_path}/{change}/{index}"
    node = root.subkey_at_private_derivation(path)
    pub = node.eckey.get_public_key_bytes(compressed=True)
    priv = node.eckey.get_secret_bytes()
    return DerivedKey(
        scheme=scheme.label, path=path,
        address=_address_for(pub, scheme.addr_type, coin, net),
        wif=_wif(priv, coin, net),
    )


# --------------------------------------------------------------------------- #
# discovery (gap-limited) — needs only a history(address)->bool probe, so it is
# unit-testable with a fake probe and live with the daemon's getaddresshistory.
# --------------------------------------------------------------------------- #

@dataclass
class Discovery:
    found: List[DerivedKey] = field(default_factory=list)
    scanned_schemes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def wifs(self) -> List[str]:
        # dedup, order-preserving (the same key can recur across overlapping schemes)
        seen, out = set(), []
        for k in self.found:
            if k.wif not in seen:
                seen.add(k.wif)
                out.append(k.wif)
        return out


def discover(
    secret: str,
    coin: dict,
    *,
    history: Callable[[str], bool],
    net: str = "mainnet",
    passphrase: str = "",
    gap: int = DEFAULT_GAP,
    max_index: int = 1000,
) -> Discovery:
    """Find funded legacy addresses for one coin. ``secret`` is either a seed phrase
    (BIP39 and/or Electrum-native) or a raw WIF / 64-hex private key. ``history(addr)``
    returns True if the address has any on-chain history."""
    d = Discovery()
    raw = secret.strip()

    # Raw private key (WIF or 64-hex): one flat key, no derivation/gap-scan. Let the
    # daemon's sweep auto-discover its address types; record it as funded-candidate.
    rawkey = _as_raw_wif(raw, coin, net)
    if rawkey is not None:
        d.scanned_schemes.append("imported-key")
        d.found.append(rawkey)
        return d

    kinds = detect_seed_kinds(raw)
    if not kinds:
        d.warnings.append("input is not a valid BIP39 or Electrum seed, nor a WIF/hex "
                          "private key — nothing scanned")
        return d
    if "electrum" in kinds and "bip39" not in kinds:
        # Electrum-native 'old' (pre-BIP32 mpk) seeds also satisfy is_new_seed==False,
        # so they never reach here; flag the residual unsupported shapes loudly.
        pass

    roots: Dict[str, BIP32Node] = {}
    if "bip39" in kinds:
        roots["bip39"] = _bip39_root(raw, passphrase)
    if "electrum" in kinds:
        roots["electrum"] = _electrum_root(raw, passphrase)

    for scheme in legacy_schemes(coin, net):
        root = roots.get(scheme.seed_kind)
        if root is None:
            continue
        d.scanned_schemes.append(scheme.label)
        for change in (0, 1):                 # receive then change
            run = 0
            for index in range(max_index):
                key = derive_at(root, scheme, change, index, coin, net)
                if history(key.address):
                    d.found.append(key)
                    run = 0
                else:
                    run += 1
                    if run >= gap:
                        break

    d.warnings.append("not scanned (sweep the old wallet manually if you used these): "
                      "old non-BIP32 'mpk' Electrum seeds, multisig, and SLIP39")
    return d


def _as_raw_wif(raw: str, coin: dict, net: str) -> Optional[DerivedKey]:
    """Return a DerivedKey if ``raw`` is a usable single private key (full WIF, a
    ``type:WIF`` import string, or 64-hex), else None."""
    candidate = raw.split(":", 1)[1] if raw[:8].lower().startswith(("p2pkh:", "p2wpkh:")) else raw
    # 64-hex private key -> coin WIF
    if len(candidate) == 64:
        try:
            priv = bytes.fromhex(candidate)
        except ValueError:
            priv = None
        if priv is not None and len(priv) == 32:
            wif = _wif(priv, coin, net)
            pub = ecc.ECPrivkey(priv).get_public_key_bytes(compressed=True)
            return DerivedKey("imported-hex", "-",
                              _address_for(pub, "p2wpkh", coin, net), wif)
    # WIF: validated by the daemon at sweep time; accept the string as-is.
    if 50 <= len(candidate) <= 53 and candidate[0] in "5KLcms9":
        return DerivedKey("imported-wif", "-", "(daemon-discovered)", raw)
    return None


# --------------------------------------------------------------------------- #
# orchestration: dry-run (preview) -> execute (broadcast) -> verify (confirm+empty)
# --------------------------------------------------------------------------- #

@dataclass
class MigrationPlan:
    ticker: str
    destination: str
    source_addresses: List[str]
    scanned_schemes: List[str]
    warnings: List[str]
    tx_hex: Optional[str] = None      # signed sweep tx, NOT yet broadcast
    num_inputs: int = 0
    amount_received: int = 0          # satoshis arriving at destination (net of fee)
    txid: Optional[str] = None        # set by execute() once broadcast
    error: Optional[str] = None       # hard failure (e.g. server unreachable) — NOT "no funds"

    @property
    def has_funds(self) -> bool:
        return self.error is None and bool(self.tx_hex) and self.amount_received > 0


class Migrator:
    """Drives a dry-run sweep -> broadcast -> confirm against one coin's online daemon."""

    def __init__(self, orchestrator):
        self.orch = orchestrator

    def _coin(self, ticker: str) -> dict:
        return self.orch.coins[ticker]

    def _history_probe(self, ticker: str) -> Callable[[str], bool]:
        def probe(address: str) -> bool:
            # A genuine UNUSED address returns [] (falsey). A connectivity/RPC failure
            # RAISES and MUST abort discovery — otherwise a server outage would be read
            # as "every address is unused" and we'd falsely report "no legacy funds".
            return bool(self.orch.rpc(ticker, "getaddresshistory", address))
        return probe

    def destination_address(self, ticker: str) -> str:
        dest = self.orch.rpc(ticker, "getunusedaddress")
        return dest or self.orch.first_address(ticker)

    def _assert_dest_for_coin(self, ticker: str, dest: str, net: str) -> None:
        hrp = _hrp(self._coin(ticker), net)
        if not dest.startswith(hrp + "1"):
            raise ValueError(f"refusing cross-chain sweep: destination {dest!r} is not a "
                             f"{ticker} address (expected {hrp}1…)")

    def plan(self, ticker: str, secret: str, *, net: str = "mainnet",
             passphrase: str = "", gap: int = DEFAULT_GAP) -> MigrationPlan:
        """Discover funded legacy keys and build (but DON'T broadcast) the sweep tx."""
        coin = self._coin(ticker)
        plan = MigrationPlan(ticker=ticker, destination="", source_addresses=[],
                             scanned_schemes=[], warnings=[])
        try:
            dest = self.destination_address(ticker)
            self._assert_dest_for_coin(ticker, dest, net)   # cross-chain guard (ValueError)
            plan.destination = dest
            disc = discover(secret, coin, history=self._history_probe(ticker),
                            net=net, passphrase=passphrase, gap=gap)
        except ValueError:
            raise   # cross-chain / programmer error: surface loudly, don't mask as "no funds"
        except Exception as e:
            # Connectivity/RPC failure: report a hard error, NEVER a silent "nothing found".
            plan.error = (f"could not reach the {ticker} server ({str(e)[:120]}); "
                          f"NOT migrated — check the connection and try again")
            return plan
        plan.source_addresses = [k.address for k in disc.found]
        plan.scanned_schemes = disc.scanned_schemes
        plan.warnings = list(disc.warnings)
        wifs = disc.wifs
        if not wifs:
            plan.warnings.append("no funded legacy addresses found — nothing to migrate")
            return plan
        if len(wifs) > SWEEP_IMAX:
            plan.warnings.append(f"{len(wifs)} candidate keys exceeds the sweep input cap "
                                 f"({SWEEP_IMAX}); a second migration pass may be needed")
        try:
            # Feed the private keys over STDIN (the `?` prompt), never argv — a WIF on a
            # daemon command line would leak via /proc/<pid>/cmdline during the sweep.
            tx_hex = self.orch.rpc(ticker, "sweep", "?", dest, stdin=" ".join(wifs) + "\n")
        except Exception as e:
            # sweep raises when no UTXOs / dust-only — surface, don't crash the flow.
            plan.warnings.append(f"sweep found nothing to move ({str(e)[:120]})")
            return plan
        if not tx_hex or not isinstance(tx_hex, str):
            plan.warnings.append("no spendable UTXOs on the discovered addresses")
            return plan
        tx = Transaction(tx_hex)
        plan.tx_hex = tx_hex
        plan.num_inputs = len(tx.inputs())
        plan.amount_received = sum(o.value for o in tx.outputs())
        return plan

    def execute(self, plan: MigrationPlan) -> str:
        """Broadcast the previewed sweep tx. Returns the txid. Does NOT mean 'done' —
        call :meth:`verify` to confirm."""
        if plan.error:
            raise ValueError(f"cannot execute a failed plan: {plan.error}")
        if not plan.has_funds:
            raise ValueError("nothing to migrate (run plan() first; no funds found)")
        plan.txid = self.orch.rpc(plan.ticker, "broadcast", plan.tx_hex)
        return plan.txid

    def verify(self, plan: MigrationPlan, *, min_confirmations: int = 1) -> dict:
        """One-shot status check, polled until ``done``. Done requires BOTH the swept
        source addresses to read empty AND the broadcast tx to reach
        ``min_confirmations`` — broadcast alone isn't enough (RBF / mempool eviction)."""
        remaining = []
        for addr in plan.source_addresses:
            if addr.startswith("("):   # imported-key placeholder, not a real address
                continue
            try:
                utxos = self.orch.rpc(plan.ticker, "getaddressunspent", addr)
            except Exception:
                utxos = None
            if utxos:
                remaining.append(addr)
        sources_emptied = not remaining
        confirmations = 0
        if plan.txid:
            try:
                # The destination is a unified-wallet address, so the tx enters that
                # wallet's db after a sync; get_tx_status raises until then (treat as 0).
                st = self.orch.rpc(plan.ticker, "get_tx_status", plan.txid)
                confirmations = int(st.get("confirmations", 0)) if isinstance(st, dict) else 0
            except Exception:
                confirmations = 0
        return {
            "ticker": plan.ticker,
            "sources_emptied": sources_emptied,
            "remaining_funded": remaining,
            "confirmations": confirmations,
            "done": sources_emptied and confirmations >= min_confirmations,
        }
