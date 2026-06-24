"""Offline tests for the sweep-based migration's derivation + discovery logic.

These need no network: discovery is driven by a fake ``history`` probe so we can
assert the gap-limit, scheme coverage, raw-key handling, and the cross-chain guard
without a daemon. The live fund->sweep->verify path is exercised separately on the
build server's regtest cluster.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from electrum.bitcoin import DecodeBase58CheckBlake  # noqa: E402

from unified import migration, provisioning  # noqa: E402

# A standard BIP39 test vector (valid checksum).
BIP39 = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


@pytest.fixture(scope="module")
def coins():
    return provisioning.load_coins()


def test_schemes_cover_electrum_and_bip39(coins):
    # BLC's own coin_type IS the inherited 10 -> no duplicate block.
    blc = migration.legacy_schemes(coins["BLC"])
    labels = [s.label for s in blc]
    assert "electrum-standard" in labels and "electrum-segwit" in labels
    assert "bip44 m/44'/10'" in labels and "bip84 m/84'/10'" in labels
    assert len(blc) == 2 + 3   # 2 electrum + 3 bip39 purposes at ct=10

    # An aux coin has BOTH the inherited ct=10 block AND its own ct block.
    bbtc = migration.legacy_schemes(coins["BBTC"])
    bbtc_labels = [s.label for s in bbtc]
    assert "bip44 m/44'/10'" in bbtc_labels          # inherited (where legacy funds are)
    assert f"bip44 m/44'/{coins['BBTC']['coin_type']}'" in bbtc_labels  # own coin_type
    assert len(bbtc) == 2 + 3 + 3


def test_detect_seed_kinds():
    assert "bip39" in migration.detect_seed_kinds(BIP39)
    assert migration.detect_seed_kinds("not a real seed phrase at all nope") == []


def test_derive_at_address_types_and_wif(coins):
    blc = coins["BLC"]
    root = migration._bip39_root(BIP39)
    by_label = {s.label: s for s in migration.legacy_schemes(blc)}

    # native segwit -> bech32 with the coin HRP
    k84 = migration.derive_at(root, by_label["bip84 m/84'/10'"], 0, 0, blc, "mainnet")
    assert k84.address.startswith("blc1")
    # legacy -> base58 p2pkh (version byte 26 for BLC)
    k44 = migration.derive_at(root, by_label["bip44 m/44'/10'"], 0, 0, blc, "mainnet")
    assert not k44.address.startswith("blc1")
    # wrapped segwit -> p2sh base58
    k49 = migration.derive_at(root, by_label["bip49 m/49'/10'"], 0, 0, blc, "mainnet")
    assert not k49.address.startswith("blc1")

    # every derived WIF decodes (blake256 base58check) to prefix + 32-byte priv + 0x01
    for k in (k84, k44, k49):
        raw = DecodeBase58CheckBlake(k.wif)
        assert raw[0] == blc["wif_prefix"] and len(raw) == 34 and raw[-1] == 0x01

    # distinct paths -> distinct keys (no accidental collapse)
    assert len({k84.wif, k44.wif, k49.wif}) == 3


def test_discover_gap_limit_finds_funded_and_stops(coins):
    blc = coins["BLC"]
    root = migration._bip39_root(BIP39)
    scheme = next(s for s in migration.legacy_schemes(blc) if s.label == "bip84 m/84'/10'")
    # Mark indices 0 and 2 (receive) of THIS scheme as funded.
    funded = {
        migration.derive_at(root, scheme, 0, 0, blc, "mainnet").address,
        migration.derive_at(root, scheme, 0, 2, blc, "mainnet").address,
    }
    calls = {"n": 0}

    def history(addr):
        calls["n"] += 1
        return addr in funded

    disc = migration.discover(BIP39, blc, history=history, gap=20)
    got = {k.address for k in disc.found}
    assert funded <= got
    # gap-limit must terminate: far fewer than the per-scheme max_index*changes*schemes
    assert calls["n"] < 2000
    # the unsupported-shapes warning is always present (never silently "complete")
    assert any("SLIP39" in w or "mpk" in w for w in disc.warnings)


def test_discover_raw_hex_key_is_single_candidate(coins):
    blc = coins["BLC"]
    priv_hex = "11" * 32

    def history(addr):
        raise AssertionError("raw key must not gap-scan / hit history")

    disc = migration.discover(priv_hex, blc, history=history)
    assert disc.scanned_schemes == ["imported-key"]
    assert len(disc.found) == 1
    assert DecodeBase58CheckBlake(disc.found[0].wif)[0] == blc["wif_prefix"]


def test_discover_garbage_input_warns_nothing_scanned(coins):
    disc = migration.discover("clearly not a seed or key", coins["BLC"], history=lambda a: False)
    assert disc.found == []
    assert any("not a valid" in w for w in disc.warnings)


class _FakeOrch:
    def __init__(self, coins):
        self.coins = coins


def test_cross_chain_destination_is_refused(coins):
    m = migration.Migrator(_FakeOrch(coins))
    # a UMO address must never be accepted as a BLC sweep destination
    with pytest.raises(ValueError, match="cross-chain"):
        m._assert_dest_for_coin("BLC", "umo1qexampleexampleexampleexampleexampleex", "mainnet")
    # the matching coin passes
    m._assert_dest_for_coin("BLC", "blc1qexampleexampleexampleexampleexampleex", "mainnet")


# --------------------------------------------------------------------------- #
# orchestration edge / break tests against a fake daemon (deterministic, no net)
# --------------------------------------------------------------------------- #

import struct  # noqa: E402


def _raw_tx(value_sats: int, n_in: int = 1) -> str:
    """Minimal valid raw tx hex with one p2wpkh-script output of `value_sats` and
    `n_in` empty-script inputs — net-independent, so Transaction(hex) parses it."""
    s = b"\x01\x00\x00\x00" + bytes([n_in])
    for _ in range(n_in):
        s += b"\x00" * 32 + b"\x00\x00\x00\x00" + b"\x00" + b"\xff\xff\xff\xff"
    spk = bytes.fromhex("0014" + "11" * 20)
    s += b"\x01" + struct.pack("<Q", value_sats) + bytes([len(spk)]) + spk + b"\x00\x00\x00\x00"
    return s.hex()


class FakeOrch:
    """Implements just the orchestrator RPC surface migration.Migrator uses."""

    def __init__(self, coins, *, funded=None, unused="blc1qunusedunusedunusedunusedunusedun",
                 sweep=None, unspent=None, history_raises=False, confs=0):
        self.coins = coins
        self._funded = set(funded or [])
        self._unused = unused
        self._sweep = sweep                 # str hex | None | Exception instance
        self._unspent = unspent or {}
        self._history_raises = history_raises
        self._confs = confs
        self.calls = []

    def rpc(self, ticker, command, *args, stdin=None):
        self.calls.append((command, args, stdin))
        if command == "getaddresshistory":
            if self._history_raises:
                raise RuntimeError("BLC getaddresshistory failed: server not reachable")
            return ["tx"] if args[0] in self._funded else []
        if command == "getunusedaddress":
            return self._unused
        if command == "sweep":
            if isinstance(self._sweep, Exception):
                raise self._sweep
            return self._sweep
        if command == "broadcast":
            return "f" * 64
        if command == "getaddressunspent":
            return self._unspent.get(args[0], [])
        if command == "get_tx_status":
            return {"confirmations": self._confs}
        raise AssertionError(f"unexpected rpc {command}")

    def first_address(self, ticker):
        return self._unused


def _funded_bip84(coins, indices, change=0):
    blc = coins["BLC"]
    root = migration._bip39_root(BIP39)
    scheme = next(s for s in migration.legacy_schemes(blc) if s.label == "bip84 m/84'/10'")
    return [migration.derive_at(root, scheme, change, i, blc, "mainnet").address for i in indices]


def test_plan_funds_found_then_execute(coins):
    funded = _funded_bip84(coins, [0, 1])
    orch = FakeOrch(coins, funded=funded, sweep=_raw_tx(12345, n_in=2))
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert plan.has_funds and plan.amount_received == 12345 and plan.num_inputs == 2
    assert set(funded) <= set(plan.source_addresses)
    assert m.execute(plan) == "f" * 64


def test_plan_empty_wallet_finds_nothing(coins):
    orch = FakeOrch(coins, funded=[])      # nothing has history
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert not plan.has_funds and plan.error is None
    assert any("no funded" in w for w in plan.warnings)
    with pytest.raises(ValueError, match="nothing to migrate"):
        m.execute(plan)


def test_plan_server_down_is_hard_error_not_no_funds(coins):
    orch = FakeOrch(coins, history_raises=True)
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert plan.error is not None and "try again" in plan.error
    assert not plan.has_funds
    # must NOT claim "no funds" — that would hide the user's coins
    assert not any("no funded" in w for w in plan.warnings)
    with pytest.raises(ValueError, match="failed plan"):
        m.execute(plan)


def test_plan_sweep_returns_none_is_not_funds(coins):
    funded = _funded_bip84(coins, [0])
    orch = FakeOrch(coins, funded=funded, sweep=None)   # addresses used but no spendable UTXOs
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert not plan.has_funds and plan.error is None
    assert any("no spendable" in w.lower() for w in plan.warnings)


def test_plan_sweep_dust_raises_is_caught(coins):
    funded = _funded_bip84(coins, [0])
    orch = FakeOrch(coins, funded=funded, sweep=Exception("Not enough funds on address."))
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert not plan.has_funds
    assert any("nothing to move" in w.lower() for w in plan.warnings)


def test_plan_refuses_cross_chain_destination(coins):
    orch = FakeOrch(coins, funded=_funded_bip84(coins, [0]), unused="umo1qwrongchainwrongchainwrongchain")
    m = migration.Migrator(orch)
    with pytest.raises(ValueError, match="cross-chain"):
        m.plan("BLC", BIP39, gap=5)


def test_plan_over_imax_warns(coins):
    funded = _funded_bip84(coins, range(0, 101))   # 101 consecutive funded -> >SWEEP_IMAX
    orch = FakeOrch(coins, funded=funded, sweep=_raw_tx(999))
    m = migration.Migrator(orch)
    plan = m.plan("BLC", BIP39, gap=5)
    assert plan.has_funds
    assert any("exceeds the sweep input cap" in w for w in plan.warnings)


def test_verify_done_requires_empty_sources_and_confirmation(coins):
    src = _funded_bip84(coins, [0, 1])
    plan = migration.MigrationPlan(ticker="BLC", destination="blc1qx", source_addresses=src,
                                   scanned_schemes=[], warnings=[], tx_hex=_raw_tx(5),
                                   amount_received=5, txid="a" * 64)
    # sources still funded -> not done, even with confirmations
    busy = migration.Migrator(FakeOrch(coins, unspent={src[0]: [{"value": "1"}]}, confs=6))
    r = busy.verify(plan)
    assert r["sources_emptied"] is False and r["done"] is False
    # sources empty but 0 confirmations (broadcast not yet mined) -> not done
    pending = migration.Migrator(FakeOrch(coins, unspent={}, confs=0))
    r = pending.verify(plan)
    assert r["sources_emptied"] is True and r["done"] is False
    # empty + confirmed -> done
    done = migration.Migrator(FakeOrch(coins, unspent={}, confs=1))
    assert done.verify(plan)["done"] is True
