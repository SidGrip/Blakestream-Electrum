"""P4 price-oracle tests: graceful None when no source; manual + provider chain;
BTC/USD valuation; portfolio split into priced/unpriced."""

from decimal import Decimal

from unified import prices


def test_empty_oracle_returns_none_everywhere():
    o = prices.PriceOracle()  # default: one empty manual provider
    assert o.price_btc("BLC") is None
    assert o.btc_usd() is None
    assert o.value_btc("BLC", Decimal("100")) is None
    assert o.value_usd("BLC", Decimal("100")) is None


def test_portfolio_all_unpriced_degrades_gracefully():
    o = prices.PriceOracle()
    pf = o.value_portfolio({"BLC": "1.5", "PHO": "0"})
    assert pf["total"]["value_btc"] is None and pf["total"]["value_usd"] is None
    assert pf["unpriced"] == ["BLC", "PHO"] and pf["priced"] == []
    # amounts are always present even with no prices
    assert pf["coins"]["BLC"]["amount"] == Decimal("1.5")
    assert pf["coins"]["BLC"]["value_btc"] is None


def test_manual_provider_partial_prices():
    mp = prices.ManualPriceProvider(
        btc_per_coin={"BLC": Decimal("0.0001")}, btc_usd_rate=Decimal("60000"))
    o = prices.PriceOracle([mp])
    assert o.value_btc("BLC", Decimal("10")) == Decimal("0.001")
    assert o.value_usd("BLC", Decimal("10")) == Decimal("60")
    # an unpriced coin stays None even though BLC + btc_usd are known
    assert o.price_btc("UMO") is None
    assert o.value_usd("UMO", Decimal("10")) is None


def test_portfolio_mixed_priced_unpriced():
    mp = prices.ManualPriceProvider(
        btc_per_coin={"BLC": Decimal("0.0001"), "PHO": Decimal("0.00002")},
        btc_usd_rate=Decimal("50000"))
    o = prices.PriceOracle([mp])
    pf = o.value_portfolio({"BLC": "2", "PHO": "5", "UMO": "100"})
    assert set(pf["priced"]) == {"BLC", "PHO"} and pf["unpriced"] == ["UMO"]
    # total_btc = 2*0.0001 + 5*0.00002 = 0.0003 ; usd = 0.0003 * 50000 = 15
    assert pf["total"]["value_btc"] == Decimal("0.0003")
    assert pf["total"]["value_usd"] == Decimal("15.0000")
    assert pf["coins"]["UMO"]["value_btc"] is None


def test_provider_chain_first_non_none_wins():
    p_lo = prices.ManualPriceProvider(btc_per_coin={"BLC": Decimal("0.001")})
    p_hi = prices.ManualPriceProvider(btc_per_coin={"BLC": Decimal("0.002")},
                                      btc_usd_rate=Decimal("70000"))
    o = prices.PriceOracle([p_lo, p_hi])
    assert o.price_btc("BLC") == Decimal("0.001")   # first provider wins
    assert o.btc_usd() == Decimal("70000")          # falls through to the one that has it


def test_from_file(tmp_path):
    import json
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"btc_per_coin": {"blc": "0.0005"}, "btc_usd": "65000"}))
    mp = prices.ManualPriceProvider.from_file(str(p))
    assert mp.price_btc("BLC") == Decimal("0.0005")  # case-insensitive
    assert mp.btc_usd() == Decimal("65000")


# --- fiat generalisation + bring-your-own HTTP sources ---

def test_dig_and_to_decimal():
    assert prices._dig({"a": {"b": "5"}}, "a.b") == "5"
    assert prices._dig({"a": [{"x": 1}, {"x": 2}]}, "a.1.x") == 2
    assert prices._dig({"a": {}}, "a.b") is None
    assert prices._dig({"a": 1}, "a.b") is None        # can't descend into a scalar
    assert prices._to_decimal("1.5") == Decimal("1.5")
    assert prices._to_decimal(None) is None
    assert prices._to_decimal(True) is None            # bools are not prices
    assert prices._to_decimal("nope") is None


def test_safe_fetch_guards_offline():
    # All reject before any socket connect (literal IPs / bad scheme) — no network.
    assert prices.safe_fetch_json("http://example.com/x") is None      # not https
    assert prices.safe_fetch_json("ftp://example.com/x") is None       # not https
    assert prices.safe_fetch_json("https://127.0.0.1/x") is None       # loopback
    assert prices.safe_fetch_json("https://10.0.0.5/x") is None        # RFC1918
    assert prices.safe_fetch_json("https://169.254.1.1/x") is None     # link-local


def test_http_template_provider_role_and_extract(monkeypatch):
    calls = []

    def fake(url, headers=None, allow_private=False, timeout=None):
        calls.append((url, headers))
        return {"bitcoin": {"usd": "65000.5"}}

    monkeypatch.setattr(prices, "safe_fetch_json", fake)
    p = prices.HttpTemplateProvider(
        role="btc_fiat",
        url_template="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies={fiat_lower}",
        json_path="bitcoin.{fiat_lower}")
    assert p.btc_fiat("USD") == Decimal("65000.5")
    assert p.price_btc("BLC") is None                  # role-gated: only btc_fiat answers
    assert "vs_currencies=usd" in calls[0][0]          # {fiat_lower} substituted


def test_http_template_api_key_header(monkeypatch):
    seen = {}

    def fake(url, headers=None, allow_private=False, timeout=None):
        seen["headers"] = headers
        return {"data": {"BLC": {"quote": {"USD": {"price": 0.0005}}}}}

    monkeypatch.setattr(prices, "safe_fetch_json", fake)
    p = prices.HttpTemplateProvider(
        role="coin_fiat",
        url_template="https://pro-api.cmc.test/quotes?symbol={coin}&convert={fiat}",
        json_path="data.{coin}.quote.{fiat}.price",
        api_key_header="X-CMC_PRO_API_KEY", api_key="secret")
    assert p.price_fiat("BLC", "USD") == Decimal("0.0005")
    assert seen["headers"]["X-CMC_PRO_API_KEY"] == "secret"   # key sent as a header


def test_value_fiat_direct_and_bridge(monkeypatch):
    def fake(url, headers=None, allow_private=False, timeout=None):
        return {"bitcoin": {"usd": "60000"}} if "vs_currencies=usd" in url else None

    monkeypatch.setattr(prices, "safe_fetch_json", fake)
    manual = prices.ManualPriceProvider(btc_per_coin={"BLC": Decimal("0.0001")})
    anchor = prices.HttpTemplateProvider(
        role="btc_fiat", url_template="https://x/price?vs_currencies={fiat_lower}",
        json_path="bitcoin.{fiat_lower}")
    o = prices.PriceOracle([manual, anchor])
    # USD served directly by the anchor: 10 * 0.0001 * 60000
    assert o.value_fiat("BLC", Decimal("10"), "USD") == Decimal("60")

    class FX:
        def rate(self, base, quote):
            return Decimal("0.9") if (base, quote) == ("USD", "EUR") else None

    # EUR not served directly -> bridge via USD * FX: 60 * 0.9
    assert o.value_fiat("BLC", Decimal("10"), "EUR", FX()) == Decimal("54")
    # an unpriced coin stays None through the whole chain
    assert o.value_fiat("UMO", Decimal("10"), "EUR", FX()) is None


def test_value_portfolio_fiat_keys():
    mp = prices.ManualPriceProvider(
        btc_per_coin={"BLC": Decimal("0.0001")}, btc_usd_rate=Decimal("50000"))
    o = prices.PriceOracle([mp])
    pf = o.value_portfolio({"BLC": "2", "UMO": "100"}, fiat="USD")
    assert pf["fiat"] == "USD"
    assert pf["coins"]["BLC"]["value_fiat"] == Decimal("10")   # 2*0.0001*50000
    assert pf["coins"]["UMO"]["value_fiat"] is None
    assert pf["priced"] == ["BLC"] and pf["unpriced"] == ["UMO"]
    assert pf["total"]["value_fiat"] == Decimal("10")


def test_cached_provider_fiat_keyed():
    """The cache must key on fiat — an EUR query must not serve the USD answer."""
    class Counting(prices.PriceProvider):
        def __init__(self):
            self.n = 0

        def btc_fiat(self, fiat):
            self.n += 1
            return Decimal("60000") if fiat.upper() == "USD" else Decimal("55000")

    inner = Counting()
    c = prices.CachedProvider(inner, ttl=999)
    assert c.btc_fiat("USD") == Decimal("60000")
    assert c.btc_fiat("EUR") == Decimal("55000")   # distinct cache entry, not the USD hit
    assert c.btc_fiat("USD") == Decimal("60000")   # served from cache
    assert inner.n == 2                              # USD + EUR computed once each
