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


# ── timestamp rendering ───────────────────────────────────────────────────────

def test_timestamp_html() -> None:
    def report_with(ts: str, rid: str = "HFW-20260712-1516") -> BreakingNewsReport:
        r = _report(rid)
        r.timestamp_display = ts
        return r

    # both live HFW templates carry a machine-readable instant
    out = bv._timestamp_html(report_with("2026-07-12 15:16 EDT"))
    check("ts html: old template gets data-utc", 'data-utc="2026-07-12T19:16:00+00:00"' in out)
    check("ts html: original text preserved", ">2026-07-12 15:16 EDT<" in out)

    out = bv._timestamp_html(report_with("August 3, 2026, 3:17 PM EDT", "HFW-20260803-1517"))
    check("ts html: new template gets data-utc", 'data-utc="2026-08-03T19:17:00+00:00"' in out)

    # 12:20 AM must be 00:20 local, not 12:20 — the classic %I trap
    out = bv._timestamp_html(report_with("July 28, 2026, 12:20 AM EDT", "HFW-20260728-0020"))
    check("ts html: midnight not noon", 'data-utc="2026-07-28T04:20:00+00:00"' in out)

    # HFW report IDs carry the prefix; the ID fallback must still work
    out = bv._timestamp_html(report_with("unparseable heading"))
    check("ts html: falls back to prefixed report id",
          'data-utc="2026-07-12T19:16:00+00:00"' in out)

    # A trailing word of 1-4 letters is indistinguishable from a zone
    # abbreviation, so it is treated as an unrecognised zone and blocks the
    # ID fallback. That is the fail-closed direction: we show the doc's raw
    # string rather than attach an offset we are only guessing at.
    out = bv._timestamp_html(report_with("unparseable heading text"))
    check("ts html: trailing short word treated as unknown zone",
          "data-utc" not in out)

    # ambiguous zone -> no data-utc at all, raw string only
    out = bv._timestamp_html(report_with("July 28, 2026, 12:20 AM AST"))
    check("ts html: ambiguous zone emits no data-utc", "data-utc" not in out)
    check("ts html: ambiguous zone still shows the text",
          "July 28, 2026, 12:20 AM AST" in out)

    # nothing usable at all -> plain escaped text
    out = bv._timestamp_html(report_with("", "not-an-id"))
    check("ts html: nothing usable emits no data-utc", "data-utc" not in out)

    # the timestamp is doc-sourced text and must stay escaped either way
    out = bv._timestamp_html(report_with("<script>alert(1)</script>", "not-an-id"))
    check("ts html: escapes raw HTML", "<script>" not in out)
    check("ts html: escapes to entities", "&lt;script&gt;" in out)

    # every report in the frozen live fixture resolves
    from breaking_news import parse_reports
    with open("breaking_news_fixture_live.txt", encoding="utf-8") as fh:
        reports, _ = parse_reports(fh.read())
    check("ts html: fixture has reports", len(reports) == 18)
    check("ts html: every fixture report resolves",
          all("data-utc" in bv._timestamp_html(r) for r in reports))


def run() -> None:
    test_compute_selection()
    test_do_fetch_and_reparse()
    test_timestamp_html()
    print()
    print(f"{_total - _failures}/{_total} passed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
