"""P4 — price oracle framework.

The six Blakestream coins are not listed on any exchange/aggregator yet, so there is
currently **no live source** to pull from. This module is the *framework*: a pluggable
provider chain with a manual/admin-set provider today, ready to accept an exchange
adapter when a market exists.

Every price is ``Optional`` on purpose. Callers MUST degrade gracefully — show coin
amounts (and BTC-denominated values where a per-coin BTC price is known); hide fiat —
whenever a price is absent. The valuation model follows the design doc: a per-coin
price in BTC, times one BTC/fiat rate, both optional and independent. The chosen display
fiat can be any currency; ``btc_fiat(fiat)`` generalises the old USD-only ``btc_usd()``.

Stdlib-only (``json``, ``decimal``, ``urllib``, ``socket``, ``ipaddress``). The manual
provider needs no network; ``HttpTemplateProvider`` / ``FrankfurterFx`` fetch over HTTPS
through an SSRF-guarded helper (https-only, private/LAN hosts blocked unless opted in).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional
from urllib import error as _urlerror, request as _urlrequest
from urllib.parse import urlsplit


class PriceProvider:
    """Interface: return None when this source has no datum (the common case today)."""

    def price_btc(self, ticker: str) -> Optional[Decimal]:
        return None

    def btc_usd(self) -> Optional[Decimal]:
        return None

    def btc_fiat(self, fiat: str) -> Optional[Decimal]:
        """1 BTC priced in ``fiat`` (ISO 4217). Generalises btc_usd()."""
        return None

    def price_fiat(self, ticker: str, fiat: str) -> Optional[Decimal]:
        """One coin priced directly in ``fiat`` (a coin_fiat source); None otherwise."""
        return None


@dataclass
class ManualPriceProvider(PriceProvider):
    """Admin-set prices. Fill this in only once a real market exists.

    JSON form (``from_file``)::

        {"btc_per_coin": {"BLC": "0.00000123", "PHO": "0.0000004"}, "btc_usd": "65000"}
    """
    btc_per_coin: Dict[str, Decimal] = field(default_factory=dict)
    btc_usd_rate: Optional[Decimal] = None

    @classmethod
    def from_file(cls, path: str) -> "ManualPriceProvider":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        per_coin = {k.upper(): Decimal(str(v))
                    for k, v in (data.get("btc_per_coin") or {}).items()}
        rate = data.get("btc_usd")
        return cls(per_coin, Decimal(str(rate)) if rate is not None else None)

    def price_btc(self, ticker: str) -> Optional[Decimal]:
        return self.btc_per_coin.get(ticker.upper())

    def btc_usd(self) -> Optional[Decimal]:
        return self.btc_usd_rate

    def btc_fiat(self, fiat: str) -> Optional[Decimal]:
        # A manually-set rate is USD-only; non-USD fiat is bridged elsewhere (CoinGecko/FX).
        return self.btc_usd_rate if (fiat or "").upper() == "USD" else None


class CachedProvider(PriceProvider):
    """Wrap any provider and cache its answers for ``ttl`` seconds, so a future
    network/exchange adapter needs no rate-limiting of its own. None answers are
    cached too — don't hammer a source that simply has no datum yet."""

    def __init__(self, provider: PriceProvider, ttl: float = 60.0, *, clock=time.monotonic):
        self.provider = provider
        self.ttl = ttl
        self._clock = clock
        self._cache: Dict[tuple, tuple] = {}   # (method, ticker, fiat) -> (value, expiry)

    def _get(self, key, compute):
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and now < hit[1]:
            return hit[0]
        value = compute()
        self._cache[key] = (value, now + self.ttl)
        return value

    def price_btc(self, ticker: str) -> Optional[Decimal]:
        return self._get(("price_btc", ticker.upper(), None), lambda: self.provider.price_btc(ticker))

    def btc_usd(self) -> Optional[Decimal]:
        return self._get(("btc_usd", None, None), self.provider.btc_usd)

    def btc_fiat(self, fiat: str) -> Optional[Decimal]:
        f = (fiat or "").upper()
        return self._get(("btc_fiat", None, f), lambda: self.provider.btc_fiat(fiat))

    def price_fiat(self, ticker: str, fiat: str) -> Optional[Decimal]:
        f = (fiat or "").upper()
        return self._get(("price_fiat", ticker.upper(), f),
                         lambda: self.provider.price_fiat(ticker, fiat))


# --- SSRF-guarded HTTPS fetch (shared by the network providers) ---

_MAX_RESPONSE = 128 * 1024   # cap reads; a hostile endpoint can't stream forever
_FETCH_TIMEOUT = 4.0         # seconds; bounds a slow source so it can't stall a poll


class _NoRedirect(_urlrequest.HTTPRedirectHandler):
    """Refuse 3xx — a redirect could bounce us to a private host past the IP check."""

    def redirect_request(self, *args, **kwargs):
        return None


def _host_is_allowed(host: str, allow_private: bool) -> bool:
    """True only if every resolved address is globally routable (or allow_private is set)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            return False
        if not ip.is_global and not allow_private:
            return False
    return True


def safe_fetch_json(url: str, headers: Optional[dict] = None,
                    allow_private: bool = False, timeout: float = _FETCH_TIMEOUT):
    """GET an HTTPS URL and return parsed JSON, or None on any failure. https-only;
    private/LAN/loopback hosts are rejected unless ``allow_private``; redirects refused;
    response size + time capped. Never raises."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    if not _host_is_allowed(parts.hostname, allow_private):
        return None
    req = _urlrequest.Request(
        url, headers={"User-Agent": "Blakestream-Electrum/1.0", **(headers or {})})
    opener = _urlrequest.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) not in (200, None):
                return None
            raw = resp.read(_MAX_RESPONSE + 1)
    except (_urlerror.URLError, OSError, ValueError):
        return None
    if len(raw) > _MAX_RESPONSE:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return None


def _dig(obj, path: str):
    """Walk a dotted path: dict keys + integer list indices. None on any miss."""
    cur = obj
    for seg in path.split("."):
        if seg == "":
            return None
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _to_decimal(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


@dataclass
class HttpTemplateProvider(PriceProvider):
    """Generic source: substitute placeholders into a URL + a dotted jsonPath, fetch,
    extract one number. ``role`` gates which method answers (coin_btc / btc_fiat /
    coin_fiat). Any failure → None (never raises)."""
    role: str
    url_template: str
    json_path: str
    coin_ids: Dict[str, str] = field(default_factory=dict)
    ids: str = ""
    api_key_header: Optional[str] = None
    api_key: Optional[str] = None
    allow_private: bool = False

    def _subst(self, s: str, ticker: Optional[str] = None, fiat: Optional[str] = None) -> str:
        coin = (ticker or "").upper()
        ids = self.coin_ids.get(coin) or self.ids or (coin.lower() if coin else "")
        return (s.replace("{coin}", coin)
                 .replace("{coin_lower}", coin.lower())
                 .replace("{fiat}", (fiat or "").upper())
                 .replace("{fiat_lower}", (fiat or "").lower())
                 .replace("{ids}", ids))

    def _fetch(self, ticker: Optional[str] = None, fiat: Optional[str] = None) -> Optional[Decimal]:
        url = self._subst(self.url_template, ticker, fiat)
        path = self._subst(self.json_path, ticker, fiat)
        headers = {}
        if self.api_key_header and self.api_key:
            headers[self.api_key_header] = self.api_key
        data = safe_fetch_json(url, headers, self.allow_private)
        if data is None:
            return None
        return _to_decimal(_dig(data, path))

    def price_btc(self, ticker: str) -> Optional[Decimal]:
        return self._fetch(ticker=ticker) if self.role == "coin_btc" else None

    def btc_fiat(self, fiat: str) -> Optional[Decimal]:
        return self._fetch(fiat=fiat) if self.role == "btc_fiat" else None

    def price_fiat(self, ticker: str, fiat: str) -> Optional[Decimal]:
        return self._fetch(ticker=ticker, fiat=fiat) if self.role == "coin_fiat" else None


class FrankfurterFx:
    """ECB fiat→fiat rates (api.frankfurter.dev): free, no key. Bridges a USD-only BTC
    anchor to the chosen fiat. Cached (rates change ~daily). Not a PriceProvider."""

    BASE = "https://api.frankfurter.dev/v1"

    def __init__(self, ttl: float = 21600.0, *, clock=time.monotonic, allow_private: bool = False):
        self.ttl = ttl
        self._clock = clock
        self.allow_private = allow_private
        self._cache: Dict[tuple, tuple] = {}

    def _get(self, key, compute):
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and now < hit[1]:
            return hit[0]
        value = compute()
        self._cache[key] = (value, now + self.ttl)
        return value

    def rate(self, base: str, quote: str) -> Optional[Decimal]:
        base = (base or "").upper()
        quote = (quote or "").upper()
        if not base or not quote:
            return None
        if base == quote:
            return Decimal(1)

        def _compute():
            data = safe_fetch_json(
                f"{self.BASE}/latest?base={base}&symbols={quote}", allow_private=self.allow_private)
            return _to_decimal(_dig(data, f"rates.{quote}")) if data else None

        return self._get(("rate", base, quote), _compute)

    def currencies(self) -> List[str]:
        def _compute():
            data = safe_fetch_json(f"{self.BASE}/currencies", allow_private=self.allow_private)
            return sorted(data.keys()) if isinstance(data, dict) else []

        return self._get(("currencies",), _compute)


class PriceOracle:
    """Queries providers in order; first non-None wins; None when nobody knows."""

    def __init__(self, providers: Optional[List[PriceProvider]] = None):
        self.providers: List[PriceProvider] = providers or [ManualPriceProvider()]

    def price_btc(self, ticker: str) -> Optional[Decimal]:
        for p in self.providers:
            v = p.price_btc(ticker)
            if v is not None:
                return v
        return None

    def btc_usd(self) -> Optional[Decimal]:
        for p in self.providers:
            v = p.btc_usd()
            if v is not None:
                return v
        return None

    def btc_fiat(self, fiat: str) -> Optional[Decimal]:
        f = (fiat or "USD").upper()
        for p in self.providers:
            v = p.btc_fiat(f)
            if v is not None:
                return v
        return self.btc_usd() if f == "USD" else None

    def price_fiat(self, ticker: str, fiat: str) -> Optional[Decimal]:
        f = (fiat or "USD").upper()
        for p in self.providers:
            v = p.price_fiat(ticker, f)
            if v is not None:
                return v
        return None

    def value_btc(self, ticker: str, amount: Decimal) -> Optional[Decimal]:
        price = self.price_btc(ticker)
        return amount * price if price is not None else None

    def value_usd(self, ticker: str, amount: Decimal) -> Optional[Decimal]:
        vb = self.value_btc(ticker, amount)
        rate = self.btc_usd()
        return vb * rate if (vb is not None and rate is not None) else None

    def value_fiat(self, ticker: str, amount: Decimal, fiat: str = "USD", fx=None) -> Optional[Decimal]:
        """One coin amount valued in ``fiat``: a direct coin→fiat source wins; else
        coin→BTC × BTC→fiat; else coin→BTC × BTC→USD × FX(USD→fiat). None when no chain
        completes."""
        f = (fiat or "USD").upper()
        direct = self.price_fiat(ticker, f)
        if direct is not None:
            return amount * direct
        pb = self.price_btc(ticker)
        if pb is None:
            return None
        bf = self.btc_fiat(f)
        if bf is not None:
            return amount * pb * bf
        if fx is not None and f != "USD":
            bu = self.btc_fiat("USD")
            if bu is not None:
                r = fx.rate("USD", f)
                if r is not None:
                    return amount * pb * bu * r
        return None

    def value_portfolio(self, balances: Dict[str, object], fiat: str = "USD", fx=None) -> dict:
        """Value a {ticker: amount} map in ``fiat``. Amounts are always present; values
        are present only where a price chain completes. ``priced``/``unpriced`` reflect the
        displayed fiat. Legacy ``value_btc``/``value_usd`` keys are retained."""
        f = (fiat or "USD").upper()
        coins: Dict[str, dict] = {}
        total_btc = Decimal(0)
        total_fiat = Decimal(0)
        any_btc = False
        any_fiat = False
        for ticker, raw_amount in balances.items():
            try:
                amount = Decimal(str(raw_amount)) if raw_amount is not None else Decimal(0)
            except (InvalidOperation, TypeError, ValueError):
                amount = Decimal(0)   # one bad/None balance can't blank the whole dashboard
            vb = self.value_btc(ticker, amount)
            vf = self.value_fiat(ticker, amount, f, fx)
            coins[ticker] = {
                "amount": amount,
                "price_btc": self.price_btc(ticker),
                "value_btc": vb,
                "value_usd": self.value_usd(ticker, amount),
                "value_fiat": vf,
            }
            if vb is not None:
                total_btc += vb
                any_btc = True
            if vf is not None:
                total_fiat += vf
                any_fiat = True
        rate = self.btc_usd()
        return {
            "coins": coins,
            "fiat": f,
            "total": {
                "value_btc": total_btc if any_btc else None,
                "value_usd": (total_btc * rate) if (any_btc and rate is not None) else None,
                "value_fiat": total_fiat if any_fiat else None,
            },
            "priced": [t for t, r in coins.items() if r["value_fiat"] is not None],
            "unpriced": [t for t, r in coins.items() if r["value_fiat"] is None],
        }
