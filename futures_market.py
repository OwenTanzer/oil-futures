"""Auditable delayed futures curves and refinery crack-spread calculations.

Market values come from explicit delivery-month contracts.  The adapter is
deliberately isolated so a licensed feed can replace Yahoo without changing
the curve, calendar-spread, or crack-spread calculations.
"""

from __future__ import annotations

import calendar
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests


MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ProductSpec:
    root: str
    name_tokens: tuple[str, ...]
    quote_unit: str
    barrel_multiplier: float


PRODUCTS: dict[str, ProductSpec] = {
    "CL": ProductSpec("CL", ("Crude Oil",), "USD/bbl", 1.0),
    "RB": ProductSpec("RB", ("RBOB Gasoline",), "USD/gal", 42.0),
    # Yahoo retains the legacy display name, while CME calls the contract ULSD.
    "HO": ProductSpec("HO", ("Heating Oil", "ULSD"), "USD/gal", 42.0),
}

CME_RULE_SOURCES = {
    "CL": "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html",
    "RB": "https://www.cmegroup.com/markets/energy/refined-products/rbob-gasoline.contractSpecs.html",
    "HO": "https://www.cmegroup.com/markets/energy/refined-products/heating-oil.contractSpecs.html",
}


class MarketDataError(RuntimeError):
    """A market-data response could not be fetched or validated."""


@dataclass(frozen=True)
class ContractQuote:
    product: str
    symbol: str
    delivery_month: str
    price: float
    volume: int | None
    quote_time: str
    trading_date: str
    session_start: str | None
    session_end: str | None
    source: str
    source_url: str
    last_trade_date: str | None = None
    is_stale: bool = False
    staleness_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuoteAdapter(Protocol):
    source_name: str
    delay_label: str

    def fetch_quote(self, product: str, delivery_month: date) -> ContractQuote:
        ...


def add_months(value: date, count: int) -> date:
    month_index = value.year * 12 + value.month - 1 + count
    return date(month_index // 12, month_index % 12 + 1, 1)


def contract_symbol(product: str, delivery_month: date) -> str:
    root = PRODUCTS[product].root
    return f"{root}{MONTH_CODES[delivery_month.month]}{delivery_month.year % 100:02d}.NYM"


def _month_label(value: date) -> str:
    return value.strftime("%Y-%m")


def _observed_fixed_holiday(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nymex_holidays(year: int) -> set[date]:
    """Full-day U.S. energy-market holidays used by CME expiry rules."""
    holidays = {
        _observed_fixed_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(date(year, 6, 19)))
    return holidays


def is_nymex_business_day(value: date) -> bool:
    if value.weekday() >= 5:
        return False
    return value not in (_nymex_holidays(value.year) | _nymex_holidays(value.year + 1))


def _previous_business_day(value: date) -> date:
    current = value - timedelta(days=1)
    while not is_nymex_business_day(current):
        current -= timedelta(days=1)
    return current


def _subtract_business_days(value: date, count: int) -> date:
    current = value
    for _ in range(count):
        current = _previous_business_day(current)
    return current


def last_trading_day(product: str, delivery_month: date) -> date:
    """Return the CME rule-derived last trading day for a CL/RB/HO contract."""
    if product not in PRODUCTS:
        raise MarketDataError(f"Unsupported product: {product}")
    month = date(delivery_month.year, delivery_month.month, 1)
    prior_month = add_months(month, -1)
    if product == "CL":
        twenty_fifth = date(prior_month.year, prior_month.month, 25)
        reference_day = twenty_fifth if is_nymex_business_day(twenty_fifth) else _previous_business_day(twenty_fifth)
        return _subtract_business_days(reference_day, 3)
    last_day = date(prior_month.year, prior_month.month, calendar.monthrange(prior_month.year, prior_month.month)[1])
    while not is_nymex_business_day(last_day):
        last_day -= timedelta(days=1)
    return last_day


def _parse_delivery_month(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise MarketDataError(f"Invalid delivery month: {value!r}") from exc


class YahooChartAdapter:
    """Delayed Yahoo Finance chart adapter for explicit NYMEX contracts."""

    source_name = "Yahoo Finance delayed chart feed"
    delay_label = "Delayed; the endpoint does not publish an exact delay"
    endpoint = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout: float = 8.0, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; MooperOilCrisis/1.0; +https://github.com/OwenTanzer/oil-futures)",
            "Accept": "application/json,text/plain,*/*",
        })

    def fetch_quote(self, product: str, delivery_month: date) -> ContractQuote:
        if product not in PRODUCTS:
            raise MarketDataError(f"Unsupported product: {product}")
        symbol = contract_symbol(product, delivery_month)
        url = self.endpoint.format(symbol=symbol)
        try:
            response = self.session.get(
                url,
                params={"interval": "5m", "range": "1d"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MarketDataError(f"{symbol}: request failed ({exc})") from exc
        if response.status_code == 429:
            raise MarketDataError(f"{symbol}: vendor rate limit (HTTP 429)")
        if response.status_code != 200:
            raise MarketDataError(f"{symbol}: vendor returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise MarketDataError(f"{symbol}: vendor returned non-JSON content")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataError(f"{symbol}: malformed JSON") from exc
        return self.parse_quote(payload, product, delivery_month, url)

    @staticmethod
    def parse_quote(
        payload: dict[str, Any], product: str, delivery_month: date, source_url: str
    ) -> ContractQuote:
        symbol = contract_symbol(product, delivery_month)
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            raise MarketDataError(f"{symbol}: missing chart object")
        if chart.get("error"):
            error = chart["error"]
            description = error.get("description", "unknown vendor error") if isinstance(error, dict) else str(error)
            raise MarketDataError(f"{symbol}: {description}")
        results = chart.get("result")
        if not isinstance(results, list) or len(results) != 1:
            raise MarketDataError(f"{symbol}: expected exactly one contract result")
        result = results[0]
        if not isinstance(result, dict):
            raise MarketDataError(f"{symbol}: contract result is not an object")
        meta = result.get("meta")
        if not isinstance(meta, dict):
            raise MarketDataError(f"{symbol}: missing metadata")

        returned_symbol = str(meta.get("symbol", ""))
        if returned_symbol.upper() != symbol:
            raise MarketDataError(f"{symbol}: response symbol mismatch ({returned_symbol!r})")
        if meta.get("instrumentType") != "FUTURE":
            raise MarketDataError(f"{symbol}: response is not a future")
        if meta.get("exchangeName") != "NYM":
            raise MarketDataError(f"{symbol}: response is not a NYMEX contract")
        if meta.get("currency") != "USD":
            raise MarketDataError(f"{symbol}: response currency is not USD")

        name = str(meta.get("longName") or meta.get("shortName") or "")
        spec = PRODUCTS[product]
        expected_month = delivery_month.strftime("%b %y")
        if not any(token.lower() in name.lower() for token in spec.name_tokens):
            raise MarketDataError(f"{symbol}: product identity mismatch ({name!r})")
        if expected_month.lower() not in name.lower():
            raise MarketDataError(f"{symbol}: delivery-month mismatch ({name!r})")

        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise MarketDataError(f"{symbol}: invalid price")
        volume = meta.get("regularMarketVolume")
        if volume is not None and (not isinstance(volume, (int, float)) or volume < 0):
            raise MarketDataError(f"{symbol}: invalid volume")

        epoch = meta.get("regularMarketTime")
        if not isinstance(epoch, (int, float)) or epoch <= 0:
            raise MarketDataError(f"{symbol}: invalid quote timestamp")
        quote_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        quote_et = quote_dt.astimezone(EASTERN)

        periods = meta.get("currentTradingPeriod")
        if periods is not None and not isinstance(periods, dict):
            raise MarketDataError(f"{symbol}: invalid trading-period metadata")
        period = (periods or {}).get("regular", {})
        if not isinstance(period, dict):
            raise MarketDataError(f"{symbol}: invalid regular-session metadata")
        session_start = _epoch_iso(period.get("start"))
        session_end = _epoch_iso(period.get("end"))

        return ContractQuote(
            product=product,
            symbol=symbol,
            delivery_month=_month_label(delivery_month),
            price=float(price),
            volume=int(volume) if volume is not None else None,
            quote_time=quote_dt.isoformat(),
            trading_date=quote_et.date().isoformat(),
            session_start=session_start,
            session_end=session_end,
            source=YahooChartAdapter.source_name,
            source_url=f"https://finance.yahoo.com/quote/{symbol}",
            last_trade_date=last_trading_day(product, delivery_month).isoformat(),
        )


def _epoch_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _is_active_session(quote: ContractQuote, now: datetime) -> bool:
    if not quote.session_start or not quote.session_end:
        return False
    start = datetime.fromisoformat(quote.session_start)
    end = datetime.fromisoformat(quote.session_end)
    return start <= now <= end


def validate_contract_active(quote: ContractQuote, now: datetime) -> ContractQuote:
    delivery_month = _parse_delivery_month(quote.delivery_month)
    expected = last_trading_day(quote.product, delivery_month)
    if quote.last_trade_date and quote.last_trade_date != expected.isoformat():
        raise MarketDataError(
            f"{quote.symbol}: last-trade-date mismatch ({quote.last_trade_date}; expected {expected.isoformat()})"
        )
    if now.astimezone(EASTERN).date() > expected:
        raise MarketDataError(f"{quote.symbol}: contract expired after {expected.isoformat()}")
    return quote if quote.last_trade_date else replace(quote, last_trade_date=expected.isoformat())


def validate_freshness(quote: ContractQuote, now: datetime) -> ContractQuote:
    quote = validate_contract_active(quote, now)
    quote_time = datetime.fromisoformat(quote.quote_time)
    age = now - quote_time
    if age < timedelta(minutes=-5):
        raise MarketDataError(f"{quote.symbol}: quote timestamp is in the future")
    if age > timedelta(days=4):
        raise MarketDataError(f"{quote.symbol}: quote is more than four days old")
    if _is_active_session(quote, now) and age > timedelta(minutes=30):
        return replace(
            quote,
            is_stale=True,
            staleness_note=f"last trade is {int(age.total_seconds() // 60)} minutes old during active session",
        )
    return quote


def validate_join(quotes: list[ContractQuote], now: datetime) -> None:
    if not quotes:
        raise MarketDataError("No quotes to join")
    trading_dates = {quote.trading_date for quote in quotes}
    if len(trading_dates) != 1:
        details = ", ".join(f"{quote.symbol}={quote.trading_date}" for quote in quotes)
        raise MarketDataError(f"Contract legs are from different trading dates ({details})")
    timestamps = [datetime.fromisoformat(quote.quote_time) for quote in quotes]
    if any(_is_active_session(quote, now) for quote in quotes):
        if max(timestamps) - min(timestamps) > timedelta(minutes=30):
            details = ", ".join(f"{quote.symbol}={quote.quote_time}" for quote in quotes)
            raise MarketDataError(f"Contract-leg timestamps differ by more than 30 minutes ({details})")


def calculate_cracks(cl: float, rb: float, ho: float) -> dict[str, float]:
    gasoline = rb * 42.0 - cl
    distillate = ho * 42.0 - cl
    three_two_one = ((2.0 * rb * 42.0) + (ho * 42.0)) / 3.0 - cl
    return {
        "gasoline": gasoline,
        "distillate": distillate,
        "three_two_one": three_two_one,
    }


@dataclass
class _CacheItem:
    created_monotonic: float
    created_at: str
    payload: Any


class MarketDataService:
    def __init__(
        self,
        adapter: QuoteAdapter | None = None,
        cache_ttl: float = 300.0,
        stale_ttl: float = 21_600.0,
        now_fn: Any = None,
    ):
        self.adapter = adapter or YahooChartAdapter()
        self.cache_ttl = cache_ttl
        self.stale_ttl = stale_ttl
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._cache: dict[tuple[str, int], _CacheItem] = {}
        self._quote_cache: dict[tuple[str, str], _CacheItem] = {}
        self._lock = threading.Lock()

    def curve(self, months: int = 12) -> dict[str, Any]:
        if months not in (6, 12, 18):
            raise MarketDataError("curve months must be 6, 12, or 18")
        return self._cached("curve", months, lambda: self._build_curve(months))

    def cracks(self, months: int = 6) -> dict[str, Any]:
        if months not in (6, 12):
            raise MarketDataError("crack months must be 6 or 12")
        return self._cached("cracks", months, lambda: self._build_cracks(months))

    def _cached(self, kind: str, months: int, builder: Any) -> dict[str, Any]:
        key = (kind, months)
        with self._lock:
            cached = self._cache.get(key)
        if cached and time.monotonic() - cached.created_monotonic <= self.cache_ttl:
            cache_age = time.monotonic() - cached.created_monotonic
            result = dict(cached.payload)
            result.update(cache_status="fresh_cache", cache_age_seconds=round(cache_age, 1))
            return result
        try:
            payload = builder()
        except MarketDataError as exc:
            if cached and time.monotonic() - cached.created_monotonic <= self.stale_ttl:
                fallback = dict(cached.payload)
                fallback["is_stale"] = True
                fallback["cache_status"] = "stale_fallback"
                fallback["cache_age_seconds"] = round(time.monotonic() - cached.created_monotonic, 1)
                fallback["warnings"] = list(fallback.get("warnings", [])) + [
                    f"Live refresh failed; showing cached data: {exc}"
                ]
                return fallback
            raise
        with self._lock:
            self._cache[key] = _CacheItem(time.monotonic(), self.now_fn().isoformat(), payload)
        return dict(payload)

    def _candidate_months(self, requested: int) -> list[date]:
        today = self.now_fn().astimezone(EASTERN).date()
        current = date(today.year, today.month, 1)
        # The common CL/RB/HO curve normally begins with next delivery month.
        # Extra candidates allow an expired/stale front contract to be skipped.
        return [add_months(current, offset) for offset in range(1, requested + 4)]

    def _fetch_many(
        self, products: tuple[str, ...], requested: int
    ) -> tuple[dict[tuple[str, str], ContractQuote], list[str], dict[tuple[str, str], str], list[date]]:
        candidates = self._candidate_months(requested)
        found: dict[tuple[str, str], ContractQuote] = {}
        warnings: list[str] = []
        failures: dict[tuple[str, str], str] = {}
        now = self.now_fn()
        pending: list[tuple[str, date]] = []
        cache_now = time.monotonic()
        with self._lock:
            for product in products:
                for month in candidates:
                    month_label = _month_label(month)
                    cached = self._quote_cache.get((product, month_label))
                    expiry = last_trading_day(product, month)
                    if now.astimezone(EASTERN).date() > expiry:
                        symbol = contract_symbol(product, month)
                        reason = f"{symbol}: contract expired after {expiry.isoformat()}"
                        failures[(product, month_label)] = reason
                        warnings.append(reason)
                        continue
                    if cached and cache_now - cached.created_monotonic <= self.cache_ttl:
                        try:
                            found[(product, month_label)] = validate_freshness(cached.payload, now)
                        except MarketDataError as exc:
                            failures[(product, month_label)] = str(exc)
                            warnings.append(str(exc))
                    else:
                        pending.append((product, month))
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self.adapter.fetch_quote, product, month): (product, month)
                for product, month in pending
            }
            for future in as_completed(futures):
                product, month = futures[future]
                symbol = contract_symbol(product, month)
                try:
                    quote = validate_freshness(future.result(), now)
                    found[(product, quote.delivery_month)] = quote
                    if quote.is_stale and quote.staleness_note:
                        warnings.append(f"{quote.symbol}: {quote.staleness_note}")
                    with self._lock:
                        self._quote_cache[(product, quote.delivery_month)] = _CacheItem(
                            time.monotonic(), self.now_fn().isoformat(), quote
                        )
                except (MarketDataError, requests.RequestException) as exc:
                    reason = str(exc)
                    if not reason.startswith(symbol):
                        reason = f"{symbol}: {reason}"
                    failures[(product, _month_label(month))] = reason
                    warnings.append(reason)
        return found, warnings, failures, candidates

    def _base_payload(self, kind: str, warnings: list[str]) -> dict[str, Any]:
        return {
            "kind": kind,
            "source": self.adapter.source_name,
            "delay": self.adapter.delay_label,
            "retrieved_at": self.now_fn().isoformat(),
            "is_stale": False,
            "cache_status": "live_fetch",
            "cache_age_seconds": 0.0,
            "contract_rule_sources": CME_RULE_SOURCES,
            "warnings": warnings,
        }

    def _build_curve(self, requested: int) -> dict[str, Any]:
        found, warnings, failures, candidates = self._fetch_many(("CL",), requested)
        quotes = sorted(
            (quote for (product, _), quote in found.items() if product == "CL"),
            key=lambda quote: quote.delivery_month,
        )[:requested]
        if len(quotes) < 2:
            raise MarketDataError("Fewer than two valid WTI contract months were available")
        last_selected = _parse_delivery_month(quotes[-1].delivery_month)
        omissions = []
        for month in candidates:
            label = _month_label(month)
            if month <= last_selected and ("CL", label) not in found:
                omissions.append({
                    "delivery_month": label,
                    "missing_legs": [contract_symbol("CL", month)],
                    "reason": failures.get(("CL", label), "No validated quote returned"),
                })
        rows = [quote.to_dict() for quote in quotes]
        spreads = []
        for front, back in zip(quotes, quotes[1:]):
            value = front.price - back.price
            spreads.append({
                "front_month": front.delivery_month,
                "back_month": back.delivery_month,
                "label": f"{front.delivery_month}/{back.delivery_month}",
                "spread": value,
                "structure": "backwardation" if value > 0 else "contango" if value < 0 else "flat",
            })
        payload = self._base_payload("curve", warnings)
        backwardated = [row for row in spreads if row["spread"] > 0]
        contangoed = [row for row in spreads if row["spread"] < 0]
        payload.update({
            "quotes": rows,
            "calendar_spreads": spreads,
            "front_price": quotes[0].price,
            "last_price": quotes[-1].price,
            "front_to_last": quotes[0].price - quotes[-1].price,
            "stale_quote_count": sum(1 for quote in quotes if quote.is_stale),
            "requested_months": requested,
            "returned_months": len(quotes),
            "omissions": omissions,
            "max_backwardation": max(backwardated, key=lambda row: row["spread"]) if backwardated else None,
            "max_contango": min(contangoed, key=lambda row: row["spread"]) if contangoed else None,
        })
        return payload

    def _build_cracks(self, requested: int) -> dict[str, Any]:
        found, warnings, failures, candidates = self._fetch_many(("CL", "RB", "HO"), requested)
        rows: list[dict[str, Any]] = []
        omissions: list[dict[str, Any]] = []
        now = self.now_fn()
        for delivery in candidates:
            month = _month_label(delivery)
            missing_products = [product for product in ("CL", "RB", "HO") if (product, month) not in found]
            if missing_products:
                missing_symbols = [contract_symbol(product, delivery) for product in missing_products]
                reasons = [
                    failures.get((product, month), f"{contract_symbol(product, delivery)}: no validated quote")
                    for product in missing_products
                ]
                omissions.append({
                    "delivery_month": month,
                    "missing_legs": missing_symbols,
                    "reason": " | ".join(reasons),
                })
                continue
            legs = [found[(product, month)] for product in ("CL", "RB", "HO")]
            try:
                validate_join(legs, now)
            except MarketDataError as exc:
                reason = f"{month}: {exc}"
                warnings.append(reason)
                omissions.append({
                    "delivery_month": month,
                    "missing_legs": [],
                    "reason": reason,
                })
                continue
            values = calculate_cracks(legs[0].price, legs[1].price, legs[2].price)
            rows.append({
                "delivery_month": month,
                "cl": legs[0].to_dict(),
                "rb": legs[1].to_dict(),
                "ho": legs[2].to_dict(),
                **values,
            })
            if len(rows) == requested:
                break
        if not rows:
            raise MarketDataError("No same-month CL/RB/HO contract sets passed validation")
        payload = self._base_payload("cracks", warnings)
        payload["rows"] = rows
        payload["requested_months"] = requested
        payload["returned_months"] = len(rows)
        payload["omissions"] = omissions
        payload["formula"] = {
            "gasoline": "RB × 42 − CL",
            "distillate": "HO × 42 − CL",
            "three_two_one": "((2 × RB × 42) + (HO × 42)) / 3 − CL",
            "unit": "USD/bbl",
        }
        return payload


_DEFAULT_SERVICE: MarketDataService | None = None


def default_market_service() -> MarketDataService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = MarketDataService()
    return _DEFAULT_SERVICE
