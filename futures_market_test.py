from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from futures_market import (
    ContractQuote,
    MarketDataError,
    MarketDataService,
    YahooChartAdapter,
    add_months,
    calculate_cracks,
    contract_symbol,
    validate_freshness,
    validate_join,
)


NOW = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)


def quote(product: str, month: date, price: float, minutes_ago: int = 5) -> ContractQuote:
    symbol = contract_symbol(product, month)
    timestamp = NOW - timedelta(minutes=minutes_ago)
    return ContractQuote(
        product=product,
        symbol=symbol,
        delivery_month=month.strftime("%Y-%m"),
        price=price,
        volume=100,
        quote_time=timestamp.isoformat(),
        trading_date=timestamp.astimezone().date().isoformat(),
        session_start=(NOW - timedelta(hours=8)).isoformat(),
        session_end=(NOW + timedelta(hours=8)).isoformat(),
        source="fixture",
        source_url="https://example.test/quote",
    )


class FakeAdapter:
    source_name = "fixture feed"
    delay_label = "fixture"

    def __init__(self):
        self.fail = False

    def fetch_quote(self, product: str, month: date) -> ContractQuote:
        if self.fail:
            raise MarketDataError("fixture outage")
        offset = (month.year - 2026) * 12 + month.month - 8
        prices = {
            "CL": 80.0 - offset,
            "RB": 2.5 - offset * 0.01,
            "HO": 3.0 - offset * 0.01,
        }
        return quote(product, month, prices[product])


class MissingLegAdapter(FakeAdapter):
    def fetch_quote(self, product: str, month: date) -> ContractQuote:
        if product == "HO" and month == date(2026, 8, 1):
            raise MarketDataError("fixture missing leg")
        return super().fetch_quote(product, month)


class FakeResponse:
    def __init__(self, status_code: int, content_type: str = "application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def json(self):
        return {"chart": {"result": [], "error": None}}


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.headers = {}

    def get(self, *args, **kwargs):
        return self.response


class FuturesMarketTest(unittest.TestCase):
    def test_month_symbols_and_year_rollover(self):
        self.assertEqual(add_months(date(2026, 12, 1), 1), date(2027, 1, 1))
        self.assertEqual(contract_symbol("CL", date(2026, 9, 1)), "CLU26.NYM")
        self.assertEqual(contract_symbol("RB", date(2027, 1, 1)), "RBF27.NYM")

    def test_crack_formulas_and_negative_values(self):
        values = calculate_cracks(cl=100.0, rb=2.0, ho=2.5)
        self.assertAlmostEqual(values["gasoline"], -16.0)
        self.assertAlmostEqual(values["distillate"], 5.0)
        self.assertAlmostEqual(values["three_two_one"], -9.0)

    def test_join_rejects_timestamp_skew(self):
        month = date(2026, 9, 1)
        legs = [quote("CL", month, 80), quote("RB", month, 2.5), quote("HO", month, 3, 40)]
        with self.assertRaisesRegex(MarketDataError, "30 minutes"):
            validate_join(legs, NOW)

    def test_thin_back_month_quote_is_retained_but_marked_stale(self):
        back_month = quote("CL", date(2027, 8, 1), 73.0, minutes_ago=90)
        checked = validate_freshness(back_month, NOW)
        self.assertTrue(checked.is_stale)
        self.assertIn("90 minutes", checked.staleness_note)

    def test_curve_contains_calendar_spreads_and_summary(self):
        service = MarketDataService(FakeAdapter(), now_fn=lambda: NOW)
        result = service.curve(6)
        self.assertEqual(len(result["quotes"]), 6)
        self.assertEqual(len(result["calendar_spreads"]), 5)
        self.assertAlmostEqual(result["front_to_last"], 5.0)
        self.assertEqual(result["calendar_spreads"][0]["structure"], "backwardation")
        self.assertIsNone(result["max_contango"])

    def test_cracks_use_only_explicit_same_month_legs(self):
        service = MarketDataService(FakeAdapter(), now_fn=lambda: NOW)
        result = service.cracks(6)
        self.assertEqual(len(result["rows"]), 6)
        first = result["rows"][0]
        self.assertEqual(first["cl"]["delivery_month"], first["rb"]["delivery_month"])
        self.assertEqual(first["rb"]["delivery_month"], first["ho"]["delivery_month"])
        self.assertAlmostEqual(first["gasoline"], 25.0)
        self.assertAlmostEqual(first["distillate"], 46.0)
        self.assertAlmostEqual(first["three_two_one"], 32.0)

    def test_missing_crack_leg_skips_only_that_delivery_month(self):
        service = MarketDataService(MissingLegAdapter(), now_fn=lambda: NOW)
        result = service.cracks(6)
        self.assertEqual(result["rows"][0]["delivery_month"], "2026-09")
        self.assertTrue(any("fixture missing leg" in warning for warning in result["warnings"]))

    def test_stale_cache_is_used_after_refresh_failure(self):
        adapter = FakeAdapter()
        service = MarketDataService(adapter, cache_ttl=-1, stale_ttl=3600, now_fn=lambda: NOW)
        fresh = service.curve(6)
        self.assertFalse(fresh["is_stale"])
        adapter.fail = True
        fallback = service.curve(6)
        self.assertTrue(fallback["is_stale"])
        self.assertIn("Live refresh failed", fallback["warnings"][-1])

    def test_yahoo_parser_rejects_symbol_mismatch(self):
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "symbol": "CL=F",
                        "instrumentType": "FUTURE",
                        "exchangeName": "NYM",
                        "currency": "USD",
                    }
                }],
            }
        }
        with self.assertRaisesRegex(MarketDataError, "symbol mismatch"):
            YahooChartAdapter.parse_quote(payload, "CL", date(2026, 9, 1), "https://example.test")

    def test_yahoo_adapter_handles_rate_limit_and_html(self):
        limited = YahooChartAdapter(session=FakeSession(FakeResponse(429)))
        with self.assertRaisesRegex(MarketDataError, "rate limit"):
            limited.fetch_quote("CL", date(2026, 9, 1))
        html_response = YahooChartAdapter(session=FakeSession(FakeResponse(200, "text/html")))
        with self.assertRaisesRegex(MarketDataError, "non-JSON"):
            html_response.fetch_quote("CL", date(2026, 9, 1))

    def test_yahoo_parser_accepts_auditable_contract_metadata(self):
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "symbol": "RBU26.NYM",
                        "instrumentType": "FUTURE",
                        "exchangeName": "NYM",
                        "currency": "USD",
                        "longName": "RBOB Gasoline Sep 26",
                        "regularMarketPrice": 3.21,
                        "regularMarketVolume": 1200,
                        "regularMarketTime": int(NOW.timestamp()),
                        "currentTradingPeriod": {"regular": {
                            "start": int((NOW - timedelta(hours=8)).timestamp()),
                            "end": int((NOW + timedelta(hours=8)).timestamp()),
                        }},
                    }
                }],
            }
        }
        parsed = YahooChartAdapter.parse_quote(payload, "RB", date(2026, 9, 1), "https://example.test")
        self.assertEqual(parsed.symbol, "RBU26.NYM")
        self.assertEqual(parsed.delivery_month, "2026-09")
        self.assertEqual(parsed.price, 3.21)


if __name__ == "__main__":
    unittest.main()
