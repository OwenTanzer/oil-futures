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
    except MarketDataError as exc:
        print(f"FAIL: {exc}")
        return 1

    curve_months = [row["delivery_month"] for row in curve["quotes"]]
    crack_months = [row["delivery_month"] for row in cracks["rows"]]
    print(f"PASS source={curve['source']}")
    print(f"curve_months={','.join(curve_months)}")
    print(f"front_to_last={curve['front_to_last']:+.2f} USD/bbl")
    print(f"crack_months={','.join(crack_months)}")
    print(f"front_3_2_1={cracks['rows'][0]['three_two_one']:.2f} USD/bbl")
    print(f"curve_warnings={len(curve['warnings'])} crack_warnings={len(cracks['warnings'])}")
    for warning in curve["warnings"][:3]:
        print(f"curve_note={warning}")
    for warning in cracks["warnings"][:3]:
        print(f"crack_note={warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
