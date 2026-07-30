"""Manual live-source smoke test for local and Railway environments."""

from __future__ import annotations

import argparse

from futures_market import MarketDataError, MarketDataService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curve-months", type=int, choices=(6, 12, 18), default=6)
    parser.add_argument("--crack-months", type=int, choices=(6, 12), default=6)
    args = parser.parse_args()
    service = MarketDataService()
    try:
        curve = service.curve(args.curve_months)
        cracks = service.cracks(args.crack_months)
        cached_curve = service.curve(args.curve_months)
    except MarketDataError as exc:
        print(f"FAIL: {exc}")
        return 1

    curve_months = [row["delivery_month"] for row in curve["quotes"]]
    crack_months = [row["delivery_month"] for row in cracks["rows"]]
    problems = []
    if curve.get("returned_months") != len(curve["quotes"]):
        problems.append("curve coverage metadata does not match returned quotes")
    if cracks.get("returned_months") != len(cracks["rows"]):
        problems.append("crack coverage metadata does not match returned rows")
    if any(not row.get("last_trade_date") for row in curve["quotes"]):
        problems.append("a curve quote is missing its rule-derived last trading day")
    if cached_curve.get("cache_status") != "fresh_cache":
        problems.append("second curve request did not disclose fresh_cache status")
    if any(len({row[leg]["delivery_month"] for leg in ("cl", "rb", "ho")}) != 1 for row in cracks["rows"]):
        problems.append("a crack row contains mismatched delivery months")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"PASS source={curve['source']}")
    print(f"curve_months={','.join(curve_months)}")
    print(f"front_to_last={curve['front_to_last']:+.2f} USD/bbl")
    print(f"crack_months={','.join(crack_months)}")
    print(f"front_3_2_1={cracks['rows'][0]['three_two_one']:.2f} USD/bbl")
    print(f"curve_warnings={len(curve['warnings'])} crack_warnings={len(cracks['warnings'])}")
    print(f"curve_cache_status={cached_curve['cache_status']} age={cached_curve['cache_age_seconds']:.1f}s")
    print(f"curve_omissions={len(curve['omissions'])} crack_omissions={len(cracks['omissions'])}")
    for warning in curve["warnings"][:3]:
        print(f"curve_note={warning}")
    for warning in cracks["warnings"][:3]:
        print(f"crack_note={warning}")
    for omission in curve["omissions"]:
        print(f"curve_omission={omission}")
    for omission in cracks["omissions"]:
        print(f"crack_omission={omission}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
