"""
Plain-assert tests for breaking_news_view.py's state-management logic —
compute_selection() and _do_fetch_and_reparse(). No pytest, matching
breaking_news_test.py's convention.

These exercise the real functions (not reimplementations): compute_selection()
has no Streamlit dependency and is called directly; _do_fetch_and_reparse()
touches only st.session_state, so the module's `st` reference is swapped for
a minimal fake exposing just that. Run with: python breaking_news_view_test.py
"""

from __future__ import annotations

import sys

import breaking_news_view as bv
from breaking_news import BreakingNewsAccessError, BreakingNewsError, BreakingNewsReport

_failures = 0
_total = 0


def check(desc: str, cond: bool) -> None:
    global _failures, _total
    _total += 1
    if cond:
        print(f"ok   {desc}")
    else:
        _failures += 1
        print(f"FAIL {desc}")


# ── compute_selection(): pure logic, no fakes needed ────────────────────────

def test_compute_selection() -> None:
    # first load: no prior state, ids present -> select newest
    sel, prev = bv.compute_selection(["c", "b", "a"], None, None)
    check("compute_selection: first load selects newest", sel == "c" and prev == "c")

    # viewer was on the previous newest -> auto-advance when a new one appears
    sel, prev = bv.compute_selection(["d", "c", "b", "a"], "c", "c")
    check("compute_selection: auto-advances when viewer was on previous newest", sel == "d" and prev == "d")

    # viewer deliberately picked an older report -> preserve it across a new newest
    sel, prev = bv.compute_selection(["d", "c", "b", "a"], "c", "a")
    check("compute_selection: preserves deliberately-selected older report", sel == "a" and prev == "d")

    # no change in newest, selection unchanged
    sel, prev = bv.compute_selection(["c", "b", "a"], "c", "b")
    check("compute_selection: no-op when newest hasn't changed", sel == "b" and prev == "c")

    # defensive: current selection no longer exists in ids at all
    sel, prev = bv.compute_selection(["c", "b", "a"], "c", "z")
    check("compute_selection: falls back to newest if selection vanished", sel == "c" and prev == "c")

    # empty ids: nothing to select, state passed through unchanged
    sel, prev = bv.compute_selection([], "c", "c")
    check("compute_selection: empty ids leaves state untouched", sel == "c" and prev == "c")


# ── _do_fetch_and_reparse(): session-state transitions via a fake `st` ─────

class FakeSessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class FakeSt:
    def __init__(self):
        self.session_state = FakeSessionState()


def _report(rid: str) -> BreakingNewsReport:
    return BreakingNewsReport(
        report_id=rid,
        headline=f"Headline {rid}",
        timestamp_display="2026-07-01 00:00 EDT",
        physical_facts=["a fact"],
        market_facts=["a price"],
        interpretation="some interpretation",
    )


def test_do_fetch_and_reparse() -> None:
    fake_st = FakeSt()
    real_st = bv.st
    bv.st = fake_st
    try:
        good_reports = [_report("HFW-2"), _report("HFW-1")]
        one_error = [BreakingNewsError(report_id="HFW-3", kind="missing_section", detail="missing Interpretation:")]

        # clean fetch populates cache, not stale
        bv.fetch_doc_text = lambda url, timeout=10.0: "irrelevant"
        bv.parse_reports = lambda text: (good_reports, [])
        bv._do_fetch_and_reparse()
        check("fetch: clean parse populates cache", fake_st.session_state.bn_cache_reports == good_reports)
        check("fetch: clean parse is not stale", fake_st.session_state.bn_stale is False)

        # a later fetch with errors must NOT clobber the existing good cache
        bv.parse_reports = lambda text: (good_reports, one_error)
        bv._do_fetch_and_reparse()
        check(
            "fetch: subsequent errors preserve existing cache (regression test)",
            fake_st.session_state.bn_cache_reports == good_reports,
        )
        check("fetch: subsequent errors mark stale", fake_st.session_state.bn_stale is True)
        check("fetch: subsequent errors recorded in bn_cache_errors", fake_st.session_state.bn_cache_errors == one_error)

        # errors only, no prior cache at all
        fake_st.session_state.clear()
        bv.parse_reports = lambda text: ([], one_error)
        bv._do_fetch_and_reparse()
        check("fetch: errors with no prior cache -> stale, no reports", fake_st.session_state.get("bn_cache_reports") is None)
        check("fetch: errors with no prior cache -> status set", "format errors only" in fake_st.session_state.bn_last_status)

        # partial parse with no prior cache: populate, not stale (data is current, errors shown separately)
        fake_st.session_state.clear()
        bv.parse_reports = lambda text: (good_reports, one_error)
        bv._do_fetch_and_reparse()
        check("fetch: partial parse with no prior cache still populates", fake_st.session_state.bn_cache_reports == good_reports)
        check("fetch: partial parse with no prior cache is not stale", fake_st.session_state.bn_stale is False)

        # access error leaves any existing cache untouched
        fake_st.session_state.clear()
        fake_st.session_state.bn_cache_reports = good_reports
        fake_st.session_state.bn_stale = False

        def _raise(url, timeout=10.0):
            raise BreakingNewsAccessError("simulated network failure")

        bv.fetch_doc_text = _raise
        bv._do_fetch_and_reparse()
        check("fetch: access error leaves existing cache untouched", fake_st.session_state.bn_cache_reports == good_reports)
        check("fetch: access error marks stale", fake_st.session_state.bn_stale is True)
        check("fetch: access error status mentions access error", "access error" in fake_st.session_state.bn_last_status)

        # no reports, no errors at all (garbage input) with no prior cache
        fake_st.session_state.clear()
        bv.fetch_doc_text = lambda url, timeout=10.0: "irrelevant"
        bv.parse_reports = lambda text: ([], [])
        bv._do_fetch_and_reparse()
        check("fetch: no reports and no errors -> stale, no valid reports status", "no valid reports" in fake_st.session_state.bn_last_status)
    finally:
        bv.st = real_st


def run() -> None:
    test_compute_selection()
    test_do_fetch_and_reparse()
    print()
    print(f"{_total - _failures}/{_total} passed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
