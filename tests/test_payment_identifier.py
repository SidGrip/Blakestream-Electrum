import os
import asyncio
from decimal import Decimal
from unittest.mock import patch

from electrum import SimpleConfig
from electrum import bitcoin
from electrum.invoices import Invoice
from electrum.payment_identifier import (
    maybe_extract_bech32_lightning_payment_identifier, PaymentIdentifier, PaymentIdentifierType,
    PaymentIdentifierState, invoice_from_payment_identifier, remove_uri_prefix,
)
from electrum.lnaddr import LnAddr, lnencode
from electrum.lnurl import LNURL6Data, LNURL3Data, LNURLError
from electrum.transaction import PartialTxOutput

from . import ElectrumTestCase
from . import restore_wallet_from_text__for_unittest
from .test_bolt11 import RHASH, PAYMENT_SECRET, PRIVKEY


class WalletMock:
    def __init__(self, electrum_path):
        self.config = SimpleConfig({
            'electrum_path': electrum_path,
            'decimal_point': 5
        })
        self.contacts = None


def _make_address(secret_hex: str, *, txin_type: str = 'p2wpkh') -> str:
    return bitcoin.address_from_private_key(
        bitcoin.serialize_privkey(bytes.fromhex(secret_hex), True, txin_type)
    )


def _make_bolt11(*, amount: Decimal = None, fallback_address: str = None, message: str = 'unit_test',
                 timestamp: int = 1707382023, expiry: int = 3600) -> str:
    tags = [('d', message), ('9', 33282)]
    if expiry is not None:
        tags.append(('x', expiry))
    if fallback_address is not None:
        tags.append(('f', fallback_address))
    return lnencode(
        LnAddr(
            date=timestamp,
            paymenthash=RHASH,
            payment_secret=PAYMENT_SECRET,
            amount=amount,
            tags=tags,
        ),
        PRIVKEY,
    )


BLAKECOIN_URI = 'blakecoin'
ADDR1 = _make_address('11' * 32, txin_type='p2wpkh')
ADDR2 = _make_address('22' * 32, txin_type='p2wpkh')
ADDR3 = _make_address('33' * 32, txin_type='p2wpkh')
ADDR4 = _make_address('44' * 32, txin_type='p2wpkh')
BASE58_ADDR = _make_address('55' * 32, txin_type='p2pkh')
BOLT11_NO_AMOUNT = _make_bolt11()
BOLT11_WITH_AMOUNT_FALLBACK = _make_bolt11(amount=Decimal('0.02'), fallback_address=BASE58_ADDR)


class TestPaymentIdentifier(ElectrumTestCase):
    def setUp(self):
        super().setUp()
        self.wallet = WalletMock(self.electrum_path)

        self.config = SimpleConfig({
            'electrum_path': self.electrum_path,
            'decimal_point': 5
        })
        self.wallet2_path = os.path.join(self.electrum_path, "somewallet2")

    def test_maybe_extract_bech32_lightning_payment_identifier(self):
        bolt11 = BOLT11_NO_AMOUNT
        lnurl = "lnurl1dp68gurn8ghj7um9wfmxjcm99e5k7telwy7nxenrxvmrgdtzxsenjcm98pjnwxq96s9"
        self.assertEqual(bolt11, maybe_extract_bech32_lightning_payment_identifier(f"{bolt11}".upper()))
        self.assertEqual(bolt11, maybe_extract_bech32_lightning_payment_identifier(f"lightning:{bolt11}"))
        self.assertEqual(bolt11, maybe_extract_bech32_lightning_payment_identifier(f"  lightning:{bolt11}   ".upper()))
        self.assertEqual(lnurl, maybe_extract_bech32_lightning_payment_identifier(lnurl))
        self.assertEqual(lnurl, maybe_extract_bech32_lightning_payment_identifier(f"  lightning:{lnurl}   ".upper()))

        self.assertEqual(None, maybe_extract_bech32_lightning_payment_identifier(f"{BLAKECOIN_URI}:{bolt11}"))
        self.assertEqual(None, maybe_extract_bech32_lightning_payment_identifier(f":{bolt11}"))
        self.assertEqual(None, maybe_extract_bech32_lightning_payment_identifier(f"garbage text"))

    def test_remove_uri_prefix(self):
        lightning, blakecoin = 'lightning', BLAKECOIN_URI
        tests = (
            (lightning, '', ''),
            (lightning, 'lightning:test', 'test'),
            (lightning, f'{blakecoin}:test', f'{blakecoin}:test'),
            (lightning, 'lightningtest', 'lightningtest'),
            (lightning, 'lightning test', 'lightning test'),
            (blakecoin, 'lightning:test', 'lightning:test'),
            (blakecoin, f'{blakecoin}:test', 'test'),
            (blakecoin, blakecoin, blakecoin),
            (blakecoin, f'{blakecoin}:', ''),
        )
        for prefix, input_str, expected_output_str in tests:
            output_str = remove_uri_prefix(input_str, prefix=prefix)
            self.assertEqual(expected_output_str, output_str, msg=output_str)
        with self.assertRaises(AssertionError):
            remove_uri_prefix(data=1234, prefix="test")

    def test_bolt11(self):
        # no amount, no fallback address
        bolt11 = BOLT11_NO_AMOUNT
        for pi_str in [
            f'{bolt11}',
            f'  {bolt11}',
            f'{bolt11}  ',
            f'lightning:{bolt11}',
            f'  lightning:{bolt11}',
            f'lightning:{bolt11}  ',
            f'lightning:{bolt11.upper()}',
            f'lightning:{bolt11}'.upper(),
        ]:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_valid())
            self.assertEqual(PaymentIdentifierType.BOLT11, pi.type)
            self.assertFalse(pi.is_amount_locked())
            self.assertFalse(pi.is_error())
            self.assertIsNotNone(pi.bolt11)

        for pi_str in [
            f'lightning:  {bolt11}',
            f'{BLAKECOIN_URI}:{bolt11}'
        ]:
            pi = PaymentIdentifier(None, pi_str)
            self.assertFalse(pi.is_valid())

        # amount, fallback address
        bolt_11_w_fallback = BOLT11_WITH_AMOUNT_FALLBACK
        pi = PaymentIdentifier(None, bolt_11_w_fallback)
        self.assertTrue(pi.is_valid())
        self.assertEqual(PaymentIdentifierType.BOLT11, pi.type)
        self.assertIsNotNone(pi.bolt11)
        self.assertTrue(pi.is_lightning())
        self.assertTrue(pi.is_onchain())
        self.assertTrue(pi.is_amount_locked())

        self.assertFalse(pi.is_error())
        self.assertFalse(pi.need_resolve())
        self.assertFalse(pi.need_finalize())
        self.assertFalse(pi.is_multiline())

    def test_bip21(self):
        bip21 = f'{BLAKECOIN_URI}:{ADDR1}?message=unit_test'
        for pi_str in [
            f'{bip21}',
            f'  {bip21}',
            f'{bip21}  ',
            f'{bip21}'.upper(),
        ]:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_available())
            self.assertFalse(pi.is_lightning())
            self.assertTrue(pi.is_onchain())
            self.assertIsNotNone(pi.bip21)

        # amount, expired, message
        bip21 = f'{BLAKECOIN_URI}:{ADDR2}?amount=0.001&message=unit_test&time=1707382023&exp=3600'

        pi = PaymentIdentifier(None, bip21)
        self.assertTrue(pi.is_available())
        self.assertFalse(pi.is_lightning())
        self.assertTrue(pi.is_onchain())
        self.assertIsNotNone(pi.bip21)

        self.assertTrue(pi.has_expired())
        self.assertEqual('unit_test', pi.bip21.get('message'))

        # amount, expired, message, lightning w matching amount
        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount=0.02&message=unit_test&time=1707382023&exp=3600&lightning={BOLT11_WITH_AMOUNT_FALLBACK}'

        pi = PaymentIdentifier(None, bip21)
        self.assertTrue(pi.is_available())
        self.assertTrue(pi.is_lightning())
        self.assertTrue(pi.is_onchain())
        self.assertIsNotNone(pi.bip21)
        self.assertIsNotNone(pi.bolt11)

        self.assertTrue(pi.has_expired())
        self.assertEqual('unit_test', pi.bip21.get('message'))

        # amount, expired, message, lightning w non-matching amount
        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount=0.01&message=unit_test&time=1707382023&exp=3600&lightning={BOLT11_WITH_AMOUNT_FALLBACK}'

        pi = PaymentIdentifier(None, bip21)
        self.assertFalse(pi.is_valid())

        # amount bounds
        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount=-1'
        pi = PaymentIdentifier(None, bip21)
        self.assertFalse(pi.is_valid())

        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount={bitcoin.TOTAL_COIN_SUPPLY_LIMIT_IN_BTC + 1}'
        pi = PaymentIdentifier(None, bip21)
        self.assertFalse(pi.is_valid())

        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount=0'
        pi = PaymentIdentifier(None, bip21)
        self.assertFalse(pi.is_valid())

    def test_lnurl_basic(self):
        """Test basic LNURL parsing without resolve"""
        valid_lnurl = 'lnurl1dp68gurn8ghj7um9wfmxjcm99e5k7telwy7nxenrxvmrgdtzxsenjcm98pjnwxq96s9'
        pi = PaymentIdentifier(None, valid_lnurl)
        self.assertTrue(pi.is_valid())
        self.assertEqual(PaymentIdentifierType.LNURL, pi.type)
        self.assertFalse(pi.is_available())
        self.assertTrue(pi.need_resolve())
        self.assertEqual(PaymentIdentifierState.NEED_RESOLVE, pi.state)

        # Test with lightning: prefix
        lightning_lnurl = f'lightning:{valid_lnurl}'
        pi = PaymentIdentifier(None, lightning_lnurl)
        self.assertTrue(pi.is_valid())
        self.assertEqual(PaymentIdentifierType.LNURL, pi.type)
        self.assertTrue(pi.need_resolve())

    @patch('electrum.payment_identifier.request_lnurl')
    def test_lnurl_pay_resolve(self, mock_request_lnurl):
        """Test LNURL-pay (LNURL6) with mocked resolve"""
        valid_lnurl = 'LNURL1DP68GURN8GHJ7MRWVF5HGUEWD3HXZERYWFJHXUEWVDHK6TMVDE6HYMRS9ANRV46DXETQPJQCS4'

        # Mock lnurl-p response
        mock_lnurl6_data = LNURL6Data(
            callback_url='https://example.com/lnurl-pay',
            max_sendable_sat=1_000_000,
            min_sendable_sat=1_000,
            metadata_plaintext='Test payment',
            comment_allowed=100,
        )
        mock_request_lnurl.return_value = mock_lnurl6_data

        pi = PaymentIdentifier(None, valid_lnurl)
        self.assertTrue(pi.need_resolve())
        self.assertEqual(PaymentIdentifierType.LNURL, pi.type)

        async def run_resolve():
            await pi._do_resolve()

        asyncio.run(run_resolve())

        self.assertEqual(PaymentIdentifierType.LNURLP, pi.type)
        self.assertEqual(PaymentIdentifierState.LNURLP_FINALIZE, pi.state)
        self.assertTrue(pi.need_finalize())
        self.assertIsNotNone(pi.lnurl_data)
        self.assertTrue(isinstance(pi.lnurl_data, LNURL6Data))
        self.assertEqual(1_000, pi.lnurl_data.min_sendable_sat)
        self.assertEqual(1_000_000, pi.lnurl_data.max_sendable_sat)
        self.assertEqual('Test payment', pi.lnurl_data.metadata_plaintext)
        self.assertEqual(100, pi.lnurl_data.comment_allowed)

    @patch('electrum.payment_identifier.request_lnurl')
    def test_lnurl_withdraw_resolve(self, mock_request_lnurl):
        """Test LNURL-withdraw (LNURL3) with mocked resolve"""
        valid_lnurl = 'LNURL1DP68GURN8GHJ7MRWVF5HGUEWD3HXZERYWFJHXUEWVDHK6TM4WPNHYCTYV4EJ7DFCVGENSDPH8QCRZETXVGCXGCMPVFJR' \
                        'WENP8P3NJEP3XE3NQWRPXFJR2VRRVSCX2V33V5UNVC3SXP3RXCFSVFSKVWPCV3SKZWTP8YUZ7AMFW35XGUNPWUHKZURF9AMRZT' \
                        'MVDE6HYMP0FETHVUNZDAMHQ7JSF4RX73TZ2VU9Z3J3GVMSLCJ57F'

        # Mock lnurl-w response
        mock_lnurl3_data = LNURL3Data(
            callback_url='https://example.com/lnurl-withdraw',
            k1='test-k1-value',
            default_description='Test withdrawal',
            min_withdrawable_sat=1_000,
            max_withdrawable_sat=500_000,
        )
        mock_request_lnurl.return_value = mock_lnurl3_data

        pi = PaymentIdentifier(None, valid_lnurl)
        self.assertTrue(pi.need_resolve())
        self.assertEqual(PaymentIdentifierType.LNURL, pi.type)

        async def run_resolve():
            await pi._do_resolve()

        asyncio.run(run_resolve())

        self.assertEqual(PaymentIdentifierType.LNURLW, pi.type)
        self.assertEqual(PaymentIdentifierState.LNURLW_FINALIZE, pi.state)
        self.assertIsNotNone(pi.lnurl_data)
        self.assertEqual('test-k1-value', pi.lnurl_data.k1)
        self.assertEqual('Test withdrawal', pi.lnurl_data.default_description)
        self.assertEqual(1000, pi.lnurl_data.min_withdrawable_sat)
        self.assertEqual(500000, pi.lnurl_data.max_withdrawable_sat)

    @patch('electrum.payment_identifier.request_lnurl')
    def test_lnurl_resolve_error(self, mock_request_lnurl):
        """Test LNURL resolve error handling"""
        lnurl = 'LNURL1DP68GURN8GHJ7MRWVF5HGUEWD3HXZERYWFJHXUEWVDHK6TM4WPNHYCTYV4EJ7DFCVGENSDPH8QCRZETXVGCXGCMPVFJR' \
                  'WENP8P3NJEP3XE3NQWRPXFJR2VRRVSCX2V33V5UNVC3SXP3RXCFSVFSKVWPCV3SKZWTP8YUZ7AMFW35XGUNPWUHKZURF9AMRZT' \
                  'MVDE6HYMP0FETHVUNZDAMHQ7JSF4RX73TZ2VU9Z3J3GVMSLCJ57F'

        # Mock LNURL error
        mock_request_lnurl.side_effect = LNURLError("Server error")

        pi = PaymentIdentifier(None, lnurl)
        self.assertTrue(pi.need_resolve())

        async def run_resolve():
            await pi._do_resolve()

        asyncio.run(run_resolve())

        self.assertEqual(PaymentIdentifierState.ERROR, pi.state)
        self.assertTrue(pi.is_error())
        self.assertIn("Server error", pi.get_error())

    def test_multiline(self):
        pi_str = '\n'.join([
            f'{ADDR1},0.01',
            f'{ADDR2},0.01',
        ])
        pi = PaymentIdentifier(self.wallet, pi_str)
        self.assertTrue(pi.is_valid())
        self.assertTrue(pi.is_multiline())
        self.assertFalse(pi.is_multiline_max())
        self.assertIsNotNone(pi.multiline_outputs)
        self.assertEqual(2, len(pi.multiline_outputs))
        self.assertTrue(all(lambda x: isinstance(x, PartialTxOutput) for x in pi.multiline_outputs))
        self.assertEqual(1000, pi.multiline_outputs[0].value)
        self.assertEqual(1000, pi.multiline_outputs[1].value)

        pi_str = '\n'.join([
            f'{ADDR1},0.01',
            f'{ADDR2},0.01',
            f'{ADDR3},!',
        ])
        pi = PaymentIdentifier(self.wallet, pi_str)
        self.assertTrue(pi.is_valid())
        self.assertTrue(pi.is_multiline())
        self.assertTrue(pi.is_multiline_max())
        self.assertIsNotNone(pi.multiline_outputs)
        self.assertEqual(3, len(pi.multiline_outputs))
        self.assertTrue(all(lambda x: isinstance(x, PartialTxOutput) for x in pi.multiline_outputs))
        self.assertEqual(1000, pi.multiline_outputs[0].value)
        self.assertEqual(1000, pi.multiline_outputs[1].value)
        self.assertEqual('!', pi.multiline_outputs[2].value)

        pi_str = '\n'.join([
            f'{ADDR1},0.01',
            f'{ADDR2},2!',
            f'{ADDR3},3!',
        ])
        pi = PaymentIdentifier(self.wallet, pi_str)
        self.assertTrue(pi.is_valid())
        self.assertTrue(pi.is_multiline())
        self.assertTrue(pi.is_multiline_max())
        self.assertIsNotNone(pi.multiline_outputs)
        self.assertEqual(3, len(pi.multiline_outputs))
        self.assertTrue(all(lambda x: isinstance(x, PartialTxOutput) for x in pi.multiline_outputs))
        self.assertEqual(1000, pi.multiline_outputs[0].value)
        self.assertEqual('2!', pi.multiline_outputs[1].value)
        self.assertEqual('3!', pi.multiline_outputs[2].value)

        pi_str = '\n'.join([
            f'{ADDR1},0.01',
            'script(OP_RETURN baddc0ffee),0'
        ])
        pi = PaymentIdentifier(self.wallet, pi_str)
        self.assertTrue(pi.is_valid())
        self.assertTrue(pi.is_multiline())
        self.assertIsNotNone(pi.multiline_outputs)
        self.assertEqual(2, len(pi.multiline_outputs))
        self.assertTrue(all(lambda x: isinstance(x, PartialTxOutput) for x in pi.multiline_outputs))
        self.assertEqual(1000, pi.multiline_outputs[0].value)
        self.assertEqual(0, pi.multiline_outputs[1].value)

    def test_spk(self):
        address = ADDR1
        for pi_str in [
            f'{address}',
            f'  {address}',
            f'{address}  ',
            f'{address}'.upper(),
        ]:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_valid())
            self.assertTrue(pi.is_available())

        spk = 'script(OP_RETURN baddc0ffee)'
        for pi_str in [
            f'{spk}',
            f'  {spk}',
            f'{spk}  ',
        ]:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_valid())
            self.assertTrue(pi.is_available())

    def test_email_and_domain(self):
        # TODO resolve mock
        domain_pi_strings = (
            'some.domain',
            'some.weird.but.valid.domain',
            'lnblcsome.weird.but.valid.domain',
            'blc1qsome.weird.but.valid.domain',
            'lnurlsome.weird.but.valid.domain',
        )
        for pi_str in domain_pi_strings:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_valid())
            self.assertEqual(PaymentIdentifierType.DOMAINLIKE, pi.type)
            self.assertFalse(pi.is_available())
            self.assertTrue(pi.need_resolve())

        email_pi_strings = (
            'user@some.domain',
            'user@some.weird.but.valid.domain',
            'lnblcuser@some.domain',
            'lnurluser@some.domain',
            'blc1quser@some.domain',
            'lightning:user@some.domain',
            'lightning:user@some.weird.but.valid.domain',
            'lightning:lnblcuser@some.domain',
            'lightning:lnurluser@some.domain',
            'lightning:blc1quser@some.domain',
        )
        for pi_str in email_pi_strings:
            pi = PaymentIdentifier(None, pi_str)
            self.assertTrue(pi.is_valid())
            self.assertEqual(PaymentIdentifierType.EMAILLIKE, pi.type)
            self.assertFalse(pi.is_available())
            self.assertTrue(pi.need_resolve())

    def test_bip70(self):
        pi_str = f'{BLAKECOIN_URI}:?r=https://test.bitpay.com/i/87iLJoaYVyJwFXtdassQJv'
        pi = PaymentIdentifier(None, pi_str)
        self.assertTrue(pi.is_valid())
        self.assertEqual(PaymentIdentifierType.BIP70, pi.type)
        self.assertFalse(pi.is_available())
        self.assertTrue(pi.need_resolve())

        # TODO resolve mock

    async def test_invoice_from_payment_identifier(self):
        # amount, expired, message, lightning w matching amount
        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?amount=0.02&message=unit_test&time=1707382023&exp=3600&lightning={BOLT11_WITH_AMOUNT_FALLBACK}'

        pi = PaymentIdentifier(None, bip21)
        invoice = invoice_from_payment_identifier(pi, None, None)
        self.assertTrue(isinstance(invoice, Invoice))
        self.assertTrue(invoice.is_lightning())
        self.assertEqual(2_000_000_000, invoice.amount_msat)

        text = 'bitter grass shiver impose acquire brush forget axis eager alone wine silver'
        d = restore_wallet_from_text__for_unittest(text, path=self.wallet2_path, config=self.config)
        wallet2 = d['wallet']  # type: Standard_Wallet

        # no amount bip21+lightning, MAX amount passed
        bip21 = f'{BLAKECOIN_URI}:{BASE58_ADDR}?message=unit_test&time=1707382023&exp=3600&lightning={BOLT11_NO_AMOUNT}'
        pi = PaymentIdentifier(None, bip21)
        invoice = invoice_from_payment_identifier(pi, wallet2, '!')
        self.assertTrue(isinstance(invoice, Invoice))
        self.assertFalse(invoice.is_lightning())

        # no amount lightning, MAX amount passed -> expect raise
        bolt11 = f'lightning:{BOLT11_NO_AMOUNT}'
        pi = PaymentIdentifier(None, bolt11)
        with self.assertRaises(AssertionError):
            invoice_from_payment_identifier(pi, wallet2, '!')
        invoice = invoice_from_payment_identifier(pi, wallet2, 1)
        self.assertEqual(1000, invoice.amount_msat)
