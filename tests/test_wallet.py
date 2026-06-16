import shutil
import tempfile
import sys
import os
import json
from decimal import Decimal
import time
from io import StringIO
import asyncio

from electrum.storage import WalletStorage
from electrum.wallet_db import FINAL_SEED_VERSION
from electrum.wallet import (Abstract_Wallet, Standard_Wallet, create_new_wallet,
                             Imported_Wallet, Wallet)
from electrum.exchange_rate import ExchangeBase, FxThread
from electrum.util import TxMinedInfo, InvalidPassword
from electrum.bitcoin import COIN
from electrum.wallet_db import WalletDB, JsonDB
from electrum.simple_config import SimpleConfig
from electrum import util
from electrum import bitcoin
from electrum.daemon import Daemon
from electrum.invoices import PR_UNPAID, PR_PAID, PR_UNCONFIRMED
from electrum.transaction import tx_from_any
from electrum.address_synchronizer import TX_HEIGHT_UNCONFIRMED
from electrum_ecc import ECPrivkey

from . import ElectrumTestCase
from . import restore_wallet_from_text__for_unittest


class FakeSynchronizer(object):

    def __init__(self, db):
        self.db = db
        self.store = []

    def add(self, address):
        self.store.append(address)


def _deterministic_imported_key(secret_hex: str, *, txin_type: str = 'p2wpkh', compressed: bool = True):
    exported_privkey = bitcoin.serialize_privkey(bytes.fromhex(secret_hex), compressed, txin_type)
    txin_type, privkey, compressed = bitcoin.deserialize_privkey(exported_privkey)
    return {
        'privkey': exported_privkey,
        'pubkey': ECPrivkey(privkey).get_public_key_hex(compressed=compressed),
        'address': bitcoin.address_from_private_key(exported_privkey),
    }


IMPORTED_P2WPKH_KEYS = tuple(
    _deterministic_imported_key(secret_hex)
    for secret_hex in (
        '11' * 32,
        '22' * 32,
        '33' * 32,
    )
)


def _imported_privkey_text(records) -> str:
    return ' '.join(record['privkey'] for record in records)


def _imported_wallet_dump(records, *, config: SimpleConfig) -> str:
    d = restore_wallet_from_text__for_unittest(
        _imported_privkey_text(records),
        path=None,
        config=config,
    )
    return d['wallet'].db.dump(human_readable=False)


class WalletTestCase(ElectrumTestCase):

    def setUp(self):
        super(WalletTestCase, self).setUp()
        self.config = SimpleConfig({'electrum_path': self.electrum_path})

        self.wallet_path = os.path.join(self.electrum_path, "somewallet")

        self._saved_stdout = sys.stdout
        self._stdout_buffer = StringIO()
        sys.stdout = self._stdout_buffer

    def tearDown(self):
        super(WalletTestCase, self).tearDown()
        # Restore the "real" stdout
        sys.stdout = self._saved_stdout


class TestWalletStorage(WalletTestCase):

    def test_read_dictionary_from_file(self):

        some_dict = {"a":"b", "c":"d"}
        contents = json.dumps(some_dict)
        with open(self.wallet_path, "w") as f:
            contents = f.write(contents)

        storage = WalletStorage(self.wallet_path)
        db = JsonDB(storage.read(), storage=storage)
        self.assertEqual("b", db.get("a"))
        self.assertEqual("d", db.get("c"))

    def test_write_dictionary_to_file(self):

        storage = WalletStorage(self.wallet_path)
        db = JsonDB('', storage=storage)

        some_dict = {
            u"a": u"b",
            u"c": u"d",
            u"seed_version": FINAL_SEED_VERSION}

        for key, value in some_dict.items():
            db.put(key, value)
        db.write()

        with open(self.wallet_path, "r") as f:
            contents = f.read()
        d = json.loads(contents)
        for key, value in some_dict.items():
            self.assertEqual(d[key], value)

    async def test_storage_imported_add_privkeys_persistence_test(self):
        text = _imported_privkey_text(IMPORTED_P2WPKH_KEYS[:2])
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, config=self.config)
        wallet = d['wallet']  # type: Imported_Wallet
        self.assertEqual(2, len(wallet.get_receiving_addresses()))
        await wallet.stop()

        # open the wallet anew again, and add a privkey. This should add the new data as a json_patch
        del wallet
        wallet = Daemon._load_wallet(self.wallet_path, password=None, config=self.config)

        wallet.import_private_keys([IMPORTED_P2WPKH_KEYS[2]['privkey']], password=None)
        self.assertEqual(3, len(wallet.get_receiving_addresses()))
        self.assertEqual(3, len(wallet.keystore.keypairs))
        await wallet.stop()

        # open the wallet anew again, and verify if the privkey was stored
        del wallet
        wallet = Daemon._load_wallet(self.wallet_path, password=None, config=self.config)
        self.assertEqual(3, len(wallet.get_receiving_addresses()))
        self.assertEqual(3, len(wallet.keystore.keypairs))
        for key in IMPORTED_P2WPKH_KEYS:
            self.assertIn(key['pubkey'], wallet.keystore.keypairs)

    async def test_storage_prevouts_by_scripthash_persistence(self):
        text = 'cycle rocket west magnet parrot shuffle foot correct salt library feed song'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, config=self.config)
        wallet1 = d['wallet']  # type: Standard_Wallet
        # create payreq
        payment_tx = tx_from_any("02000000000101a97a9ae7fb1a9220fdd170a974987ac24631dcff89b60fa4907c78c3639994db0000000000fdffffff0210270000000000001976a914ea7804a2c266063572cc009a63dc25dcc0e9d9b588ac20491e0000000000160014b8e4fdc91593b67de2bf214694ef47e38dc2ee8e02473044022005326882904906cfa9c1de75333ace1019596f2ab25d21118220d037dfc0e48b02207d0b3f075cfe5e1e0247ff3cdd7155dc05e7459daf1bfa0ea02e9112b9151ec90121026cc6a74c2b0e38661d341ffae48fe7dde5196ca4afe95d28b496673fa4cf646700000000")
        addr = wallet1.get_unused_address()
        self.assertEqual(payment_tx.outputs()[0].address, addr)
        pr_key = wallet1.create_request(amount_sat=10000, message="msg", address=addr, exp_delay=86400)
        pr = wallet1.get_request(pr_key)
        self.assertIsNotNone(pr)
        self.assertEqual(PR_UNPAID, wallet1.get_invoice_status(pr))
        await wallet1.stop()

        # open the wallet anew again, and get paid onchain
        del wallet1
        wallet1 = Daemon._load_wallet(self.wallet_path, password=None, config=self.config)
        wallet1.adb.receive_tx_callback(payment_tx, tx_height=TX_HEIGHT_UNCONFIRMED)
        self.assertEqual(PR_UNCONFIRMED, wallet1.get_invoice_status(pr))
        await wallet1.stop()

        # open the wallet anew again, and verify payreq is still paid
        del wallet1
        wallet1 = Daemon._load_wallet(self.wallet_path, password=None, config=self.config)
        self.assertEqual(PR_UNCONFIRMED, wallet1.get_invoice_status(pr))


class FakeExchange(ExchangeBase):
    def __init__(self, rate):
        super().__init__(lambda self: None, lambda self: None)
        self._quotes = {'TEST': rate}
        self._quotes_timestamp = float("inf")  # spot price from the far future never becomes stale :P

class FakeFxThread:
    def __init__(self, exchange):
        self.exchange = exchange
        self.ccy = 'TEST'

    remove_thousands_separator = staticmethod(FxThread.remove_thousands_separator)
    timestamp_rate = FxThread.timestamp_rate
    ccy_amount_str = FxThread.ccy_amount_str
    history_rate = FxThread.history_rate

class FakeADB:
    def get_tx_height(self, txid):
        # because we use a current timestamp, and history is empty,
        # FxThread.history_rate will use spot prices
        return TxMinedInfo(_height=10, conf=10, timestamp=int(time.time()), header_hash='def')

class FakeWallet:
    def __init__(self, fiat_value):
        super().__init__()
        self.fiat_value = fiat_value
        self.db = WalletDB('', storage=None, upgrade=False)
        self.adb = FakeADB()
        self.db.transactions = self.db.verified_tx = {'abc':'Tx'}

    default_fiat_value = Abstract_Wallet.default_fiat_value
    price_at_timestamp = Abstract_Wallet.price_at_timestamp
    class storage:
        put = lambda self, x: None

txid = 'abc'
ccy = 'TEST'

class TestFiat(ElectrumTestCase):
    def setUp(self):
        super().setUp()
        self.value_sat = COIN
        self.fiat_value = {}
        self.wallet = FakeWallet(fiat_value=self.fiat_value)
        self.fx = FakeFxThread(FakeExchange(Decimal('1000.001')))
        default_fiat = Abstract_Wallet.default_fiat_value(self.wallet, txid, self.fx, self.value_sat)
        self.assertEqual(Decimal('1000.001'), default_fiat)
        self.assertEqual('1 000.00', self.fx.ccy_amount_str(default_fiat, add_thousands_sep=True))

    def test_save_fiat_and_reset(self):
        self.assertEqual(False, Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, '1000.01', self.fx, self.value_sat))
        saved = self.fiat_value[ccy][txid]
        self.assertEqual('1 000.01', self.fx.ccy_amount_str(Decimal(saved), add_thousands_sep=True))
        self.assertEqual(True,       Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, '', self.fx, self.value_sat))
        self.assertNotIn(txid, self.fiat_value[ccy])
        # even though we are not setting it to the exact fiat value according to the exchange rate, precision is truncated away
        self.assertEqual(True, Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, '1 000.002', self.fx, self.value_sat))

    def test_too_high_precision_value_resets_with_no_saved_value(self):
        self.assertEqual(True, Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, '1 000.001', self.fx, self.value_sat))

    def test_empty_resets(self):
        self.assertEqual(True, Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, '', self.fx, self.value_sat))
        self.assertNotIn(ccy, self.fiat_value)

    def test_save_garbage(self):
        self.assertEqual(False, Abstract_Wallet.set_fiat_value(self.wallet, txid, ccy, 'garbage', self.fx, self.value_sat))
        self.assertNotIn(ccy, self.fiat_value)


class TestCreateRestoreWallet(WalletTestCase):

    async def test_create_new_wallet(self):
        passphrase = 'mypassphrase'
        password = 'mypassword'
        encrypt_file = True
        d = create_new_wallet(path=self.wallet_path,
                              passphrase=passphrase,
                              password=password,
                              encrypt_file=encrypt_file,
                              gap_limit=1,
                              gap_limit_for_change=1,
                              config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet

        # lightning initialization
        self.assertTrue(wallet.db.get('lightning_xprv').startswith('zprv'))

        wallet.check_password(password)
        self.assertEqual(passphrase, wallet.keystore.get_passphrase(password))
        self.assertEqual(d['seed'], wallet.keystore.get_seed(password))
        self.assertEqual(encrypt_file, wallet.storage.is_encrypted())

    async def test_restore_wallet_from_text_mnemonic(self):
        text = 'bitter grass shiver impose acquire brush forget axis eager alone wine silver'
        passphrase = 'mypassphrase'
        password = 'mypassword'
        encrypt_file = True
        d = restore_wallet_from_text__for_unittest(
            text,
            path=self.wallet_path,
            passphrase=passphrase,
            password=password,
            encrypt_file=encrypt_file,
            gap_limit=1,
            config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet
        self.assertEqual(passphrase, wallet.keystore.get_passphrase(password))
        self.assertEqual(text, wallet.keystore.get_seed(password))
        self.assertEqual(encrypt_file, wallet.storage.is_encrypted())
        self.assertEqual('blc1q2ccr34wzep58d4239tl3x3734ttle92awnp77g', wallet.get_receiving_addresses()[0])

    async def test_restore_wallet_from_text_no_storage(self):
        text = 'bitter grass shiver impose acquire brush forget axis eager alone wine silver'
        d = restore_wallet_from_text__for_unittest(
            text,
            path=None,
            gap_limit=1,
            config=self.config,
        )
        wallet = d['wallet']  # type: Standard_Wallet
        self.assertEqual(None, wallet.storage)
        self.assertEqual(text, wallet.keystore.get_seed(None))
        self.assertEqual('blc1q3g5tmkmlvxryhh843v4dz026avatc0zz2e45l0', wallet.get_receiving_addresses()[0])

    async def test_restore_wallet_from_text_xpub(self):
        text = 'zpub6nydoME6CFdJtMpzHW5BNoPz6i6XbeT9qfz72wsRqGdgGEYeivso6xjfw8cGcCyHwF7BNW4LDuHF35XrZsovBLWMF4qXSjmhTXYiHbWqGLt'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, gap_limit=1, config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet
        self.assertEqual(text, wallet.keystore.get_master_public_key())
        self.assertEqual('blc1q2ccr34wzep58d4239tl3x3734ttle92awnp77g', wallet.get_receiving_addresses()[0])

    async def test_restore_wallet_from_text_xkey_that_is_also_a_valid_electrum_seed_by_chance(self):
        text = 'yprvAJBpuoF4FKpK92ofzQ7ge6VJMtorow3maAGPvPGj38ggr2xd1xCrC9ojUVEf9jhW5L9SPu6fU2U3o64cLrRQ83zaQGNa6YP3ajZS6hHNPXj'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, gap_limit=1, config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet
        self.assertEqual(text, wallet.keystore.get_master_private_key(password=None))
        self.assertEqual('4CFGfsyckcSbCu4qsYDDDMw8vdpRkevMNu', wallet.get_receiving_addresses()[0])

    async def test_restore_wallet_from_text_xprv(self):
        text = 'zprvAZzHPqhCMt51fskXBUYB1fTFYgG3CBjJUT4WEZTpGw6hPSDWBPZYZARC5sE9xAcX8NeWvvucFws8vZxEa65RosKAhy7r5MsmKTxr3hmNmea'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, gap_limit=1, config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet
        self.assertEqual(text, wallet.keystore.get_master_private_key(password=None))
        self.assertEqual('blc1q2ccr34wzep58d4239tl3x3734ttle92awnp77g', wallet.get_receiving_addresses()[0])

    async def test_restore_wallet_from_text_addresses(self):
        text = 'blc1q2ccr34wzep58d4239tl3x3734ttle92awnp77g blc1qnp78h78vp92pwdwq5xvh8eprlga5q8gune8ld7'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, config=self.config)
        wallet = d['wallet']  # type: Imported_Wallet
        self.assertEqual('blc1q2ccr34wzep58d4239tl3x3734ttle92awnp77g', wallet.get_receiving_addresses()[0])
        self.assertEqual(2, len(wallet.get_receiving_addresses()))
        # also test addr deletion
        wallet.delete_address('blc1qnp78h78vp92pwdwq5xvh8eprlga5q8gune8ld7')
        self.assertEqual(1, len(wallet.get_receiving_addresses()))

    async def test_restore_wallet_from_text_privkeys(self):
        records = IMPORTED_P2WPKH_KEYS[:2]
        text = _imported_privkey_text(records)
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet_path, config=self.config)
        wallet = d['wallet']  # type: Imported_Wallet
        addresses = wallet.get_receiving_addresses()
        self.assertEqual(2, len(addresses))
        expected = {record['address']: record['privkey'] for record in records}
        self.assertEqual(set(expected), set(addresses))
        for addr in addresses:
            self.assertEqual(expected[addr], wallet.export_private_key(addr, password=None))
        # also test addr deletion
        wallet.delete_address(addresses[1])
        self.assertEqual(1, len(wallet.get_receiving_addresses()))


class TestWalletPassword(WalletTestCase):

    async def test_update_password_of_imported_wallet(self):
        wallet_str = _imported_wallet_dump(IMPORTED_P2WPKH_KEYS, config=self.config)
        storage = WalletStorage(self.wallet_path)
        db = WalletDB(wallet_str, storage=storage, upgrade=True)
        wallet = Wallet(db, config=self.config)

        wallet.check_password(None)

        wallet.update_password(None, "1234")

        with self.assertRaises(InvalidPassword):
            wallet.check_password(None)
        with self.assertRaises(InvalidPassword):
            wallet.check_password("wrong password")
        wallet.check_password("1234")

    async def test_update_password_of_standard_wallet(self):
        d = restore_wallet_from_text__for_unittest(
            'cereal wise two govern top pet frog nut rule sketch bundle logic',
            path=self.wallet_path,
            gap_limit=1,
            config=self.config,
        )
        wallet = d['wallet']  # type: Standard_Wallet

        wallet.check_password(None)

        wallet.update_password(None, "1234")
        with self.assertRaises(InvalidPassword):
            wallet.check_password(None)
        with self.assertRaises(InvalidPassword):
            wallet.check_password("wrong password")
        wallet.check_password("1234")

    async def test_update_password_of_standard_wallet_oldseed(self):
        d = restore_wallet_from_text__for_unittest(
            "powerful random nobody notice nothing important anyway look away hidden message over", path=self.wallet_path, config=self.config)
        wallet = d['wallet']  # type: Standard_Wallet

        wallet.check_password(None)

        wallet.update_password(None, "1234")
        with self.assertRaises(InvalidPassword):
            wallet.check_password(None)
        with self.assertRaises(InvalidPassword):
            wallet.check_password("wrong password")
        wallet.check_password("1234")

    async def test_update_password_with_app_restarts(self):
        wallet_str = _imported_wallet_dump(IMPORTED_P2WPKH_KEYS, config=self.config)
        storage = WalletStorage(self.wallet_path)
        db = WalletDB(wallet_str, storage=storage, upgrade=True)
        wallet = Wallet(db, config=self.config)
        await wallet.stop()

        storage = WalletStorage(self.wallet_path)
        # if storage.is_encrypted():
        #     storage.decrypt(password)
        db = WalletDB(storage.read(), storage=storage, upgrade=True)
        wallet = Wallet(db, config=self.config)

        wallet.check_password(None)

        wallet.update_password(None, "1234")
        with self.assertRaises(InvalidPassword):
            wallet.check_password(None)
        with self.assertRaises(InvalidPassword):
            wallet.check_password("wrong password")
        wallet.check_password("1234")
