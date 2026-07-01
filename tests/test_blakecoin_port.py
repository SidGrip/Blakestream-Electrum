import json
import subprocess
import sys
import tempfile
from pathlib import Path

from electrum_ecc import ECPrivkey

from electrum import constants
from electrum.bip32 import BIP32Node
from electrum.bitcoin import (
    address_from_private_key,
    deserialize_privkey,
    ecdsa_sign_usermessage,
    is_address,
    serialize_privkey,
    verify_usermessage_with_address,
)
from electrum.crypto import (
    blakecoin_lntx_sighash,
    blakecoin_segwit_sighash,
    sha256,
    sha256d,
)
from electrum.keystore import is_address_list, is_private_key_list
from electrum.simple_config import SimpleConfig
from electrum.transaction import Transaction

from . import ElectrumTestCase, restore_wallet_from_text__for_unittest


class TestBlakecoinPort(ElectrumTestCase):
    @staticmethod
    def _run_cli(*args, electrum_dir: str):
        repo_root = Path(__file__).resolve().parents[1]
        cmd = [sys.executable, str(repo_root / "run_electrum"), "-o", "-D", electrum_dir, *args]
        return subprocess.run(
            cmd,
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        )

    def test_mainnet_constants(self):
        self.assertEqual("blc", constants.net.SEGWIT_HRP)
        self.assertEqual(26, constants.net.ADDRTYPE_P2PKH)
        self.assertEqual(7, constants.net.ADDRTYPE_P2SH)
        self.assertEqual(128, constants.net.WIF_PREFIX)
        self.assertEqual(10, constants.net.BIP44_COIN_TYPE)
        self.assertEqual(
            "000000ba5cae4648b1a2b823f84cc3424e5d96d7234b39c6bb42800b2c7639be",
            constants.net.GENESIS,
        )

    def test_testnet_constants(self):
        old_net = constants.net
        try:
            constants.BitcoinTestnet.set_as_network()
            self.assertEqual("tblc", constants.net.SEGWIT_HRP)
            self.assertEqual(142, constants.net.ADDRTYPE_P2PKH)
            self.assertEqual(170, constants.net.ADDRTYPE_P2SH)
            self.assertEqual(239, constants.net.WIF_PREFIX)
        finally:
            old_net.set_as_network()

    def test_blakecoin_wif_address_and_message_roundtrip(self):
        secret = bytes.fromhex("01" * 32)
        wif = serialize_privkey(secret, compressed=True, txin_type="p2wpkh")
        self.assertEqual(
            "p2wpkh:KwFfNUhSDaASSAwtG7ssQM1uVX8RgX5GHWnnLfhfiQDigjkCFgSW",
            wif,
        )
        txin_type, secret_out, compressed = deserialize_privkey(wif)
        self.assertEqual("p2wpkh", txin_type)
        self.assertEqual(secret, secret_out)
        self.assertTrue(compressed)

        address = address_from_private_key(wif)
        self.assertEqual("blc1q0xcqpzrky6eff2g52qdye53xkk9jxkvr7exjxg", address)
        self.assertTrue(is_address(address))

        sig = ecdsa_sign_usermessage(
            ECPrivkey(secret_out), b"blakecoin-unit-test", is_compressed=compressed
        )
        self.assertTrue(verify_usermessage_with_address(address, sig, b"blakecoin-unit-test"))
        self.assertFalse(verify_usermessage_with_address(address, sig, b"wrong-message"))

    def test_bip32_xpub_roundtrip_uses_standard_base58check(self):
        xpub = (
            "xpub6H1LXWLaKsWFhvm6RVpEL9P4KfRZSW7abD2ttkWP3SSQvnyA8FSVqNTEcYFgJS2UaFcxupHiYkro49S8yGasTvXEYBVPamhGW6cFJodrTHy"
        )
        self.assertEqual(xpub, BIP32Node.from_xkey(xpub).to_xpub())

    def test_blakecoin_hash_policy_split(self):
        payload = b"abc"
        self.assertEqual(sha256(payload), blakecoin_lntx_sighash(payload))
        self.assertEqual(sha256d(payload), blakecoin_segwit_sighash(payload))

    def test_blakecoin_txid_policy(self):
        legacy_raw = (
            "0200000001191601a44a81e061502b7bfbc6eaa1cef6d1e6af5308ef96c9342f71dbf4b9b500000000"
            "6b483045022100a6d44d0a651790a477e75334adfb8aae94d6612d01187b2c02526e340a7fd6c80220"
            "28bdf7a64a54906b13b145cd5dab21a26bd4b85d6044e9b97bceab5be44c2a9201210253e8e0254b0c"
            "95776786e40984c1aa32a7d03efa6bdacdea5f421b774917d346feffffff026b20fa04000000001976"
            "a914024db2e87dd7cfd0e5f266c5f212e21a31d805a588aca0860100000000001976a91421919b94ae5c"
            "efcdf0271191459157cdb41c4cbf88aca6240700"
        )
        tx = Transaction(legacy_raw)
        self.assertEqual(
            "811771927e8b4080271f6904b6390a7afeadf3d2fc41e6a63dcb700a35315d23",
            tx.txid(),
        )
        self.assertEqual(tx.txid(), tx.wtxid())

        segwit_raw = (
            "01000000000101b66d722484f2db63e827ebf41d02684fed0c6550e85015a6c9d41ef216a8a6f000000"
            "00000fdffffff0280c3c90100000000160014b65ce60857f7e7892b983851c2a8e3526d09e4ab64bac"
            "30400000000160014c478ebbc0ab2097706a98e10db7cf101839931c4024730440220789c7d47f87663"
            "8c58d98733c30ae9821c8fa82b470285dcdf6db5994210bf9f02204163418bbc44af701212ad42d884"
            "cc613f3d3d831d2d0cc886f767cca6e0235e012103083a6dc250816d771faa60737bfe78b23ad619f6"
            "b458e0a1f1688e3a0605e79c00000000"
        )
        tx = Transaction(segwit_raw)
        self.assertEqual(
            "0910b216a39603f098a2650a235ead7809dd259ee5b41c58688df99b51e76e21",
            tx.txid(),
        )
        self.assertEqual(
            "bad0a7e9f9ecf65c18c4edd2b6d10ed4089be06cc035c57505c9c89e503146df",
            tx.wtxid(),
        )

    def test_cli_wallet_smoke_mainnet(self):
        with tempfile.TemporaryDirectory(prefix="blc-electrum-smoke-") as electrum_dir:
            seed = self._run_cli("make_seed", "--seed_type", "segwit", electrum_dir=electrum_dir).stdout.strip()
            self._run_cli("restore", "-w", f"{electrum_dir}/wallet1", seed, electrum_dir=electrum_dir)

            addresses = json.loads(
                self._run_cli(
                    "-w",
                    f"{electrum_dir}/wallet1",
                    "listaddresses",
                    "--receiving",
                    electrum_dir=electrum_dir,
                ).stdout
            )
            self.assertTrue(addresses)
            address = addresses[0]
            self.assertTrue(address.startswith("blc1"))

            self.assertEqual(
                "true",
                self._run_cli("validateaddress", address, electrum_dir=electrum_dir).stdout.strip(),
            )

            sig = self._run_cli(
                "-w",
                f"{electrum_dir}/wallet1",
                "signmessage",
                "--password",
                "",
                address,
                "blakecoin-electrum-smoke",
                electrum_dir=electrum_dir,
            ).stdout.strip()
            self.assertEqual(
                "true",
                self._run_cli(
                    "verifymessage",
                    address,
                    sig,
                    "blakecoin-electrum-smoke",
                    electrum_dir=electrum_dir,
                ).stdout.strip(),
            )

            wif_output = self._run_cli(
                "-w",
                f"{electrum_dir}/wallet1",
                "getprivatekeys",
                "--password",
                "",
                address,
                electrum_dir=electrum_dir,
            ).stdout.splitlines()
            wif = wif_output[-1].strip()
            self.assertTrue(wif.startswith("p2wpkh:"))

            self._run_cli("restore", "-w", f"{electrum_dir}/wallet2", wif, electrum_dir=electrum_dir)
            imported_addresses = json.loads(
                self._run_cli(
                    "-w",
                    f"{electrum_dir}/wallet2",
                    "listaddresses",
                    "--receiving",
                    electrum_dir=electrum_dir,
                ).stdout
            )
            self.assertEqual([address], imported_addresses)

    def test_cli_wallet_smoke_testnet(self):
        with tempfile.TemporaryDirectory(prefix="blc-electrum-testnet-") as electrum_dir:
            seed = self._run_cli(
                "--testnet", "make_seed", "--seed_type", "segwit", electrum_dir=electrum_dir
            ).stdout.strip()
            self._run_cli(
                "--testnet",
                "restore",
                "-w",
                f"{electrum_dir}/wallet1",
                seed,
                electrum_dir=electrum_dir,
            )
            addresses = json.loads(
                self._run_cli(
                    "--testnet",
                    "-w",
                    f"{electrum_dir}/wallet1",
                    "listaddresses",
                    "--receiving",
                    electrum_dir=electrum_dir,
                ).stdout
            )
            self.assertTrue(addresses)
            self.assertTrue(addresses[0].startswith("tblc1"))
            self.assertEqual(
                "true",
                self._run_cli(
                    "--testnet", "validateaddress", addresses[0], electrum_dir=electrum_dir
                ).stdout.strip(),
            )

    async def test_restore_wallet_from_blakecoin_text_inputs(self):
        wif = "p2wpkh:KwFfNUhSDaASSAwtG7ssQM1uVX8RgX5GHWnnLfhfiQDigjkCFgSW"
        address = "blc1q0xcqpzrky6eff2g52qdye53xkk9jxkvr7exjxg"
        self.assertTrue(is_private_key_list(wif, allow_spaces_inside_key=False))
        self.assertTrue(is_address_list(address))

        config = SimpleConfig({"electrum_path": self.electrum_path})

        imported_priv = restore_wallet_from_text__for_unittest(
            wif,
            path=str(Path(self.electrum_path) / "imported-priv-wallet"),
            config=config,
        )["wallet"]
        self.assertEqual([address], imported_priv.get_receiving_addresses())

        imported_watch = restore_wallet_from_text__for_unittest(
            address,
            path=str(Path(self.electrum_path) / "watch-only-wallet"),
            config=config,
        )["wallet"]
        self.assertEqual([address], imported_watch.get_receiving_addresses())
