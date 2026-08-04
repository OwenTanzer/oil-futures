"""
Plain-assert tests for the HSN parser in node_status_view.py.
No pytest, no test framework dependency. Run with: python node_status_test.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest import mock

import node_status_view as nsv
from node_status_view import (
    HsnSnapshot,
    NodeEntry,
    NodeStatusAccessError,
    _render_node_card,
    fetch_hsn_text,
    parse_latest_snapshot,
    parse_report_timestamp,
)

LIVE_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "node_status_fixture_live.txt")

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


# ── fixtures ──────────────────────────────────────────────────────────────────

SINGLE_REPORT = """\
HSN-20260804-0900 --- Hormuz choke tightening, Kharg export path contested --- 2026-08-04 09:00 EDT

Changes since last report:
VLCC spacing at Kharg reduced from 48h to 72h delay cycle.

Network concentration risk:
Kharg Island and Jask terminal now handling over 85% of remaining Iranian crude exports.

Node status:
Kharg Island Export Terminal | Category 2 | Precautionary loading halt; two VLCC berths closed pending drone threat assessment | Drone-proof loading schedule under review
Jask Terminal | Category 4 | Absorbing diverted Kharg volumes at 130% rated capacity | No bypass route available; single-point failure risk
Ras Tanura | Category 3 | Intact and operational but all tankers rerouted via Cape of Good Hope due to war-risk insurance exclusions | None
Fujairah Anchorage | Category 1 | Struck by ballistic missile; two berths destroyed, terminal partially operational | Emergency offloading shifted to Khor Fakkan

Sources:
gCaptain, Marine Insight, Lloyd's List.
"""

TWO_REPORTS = """\
HSN-20260803-1800 --- Earlier snapshot --- 2026-08-03 18:00 EDT

Node status:
Old Node | Category 1 | Earlier status | Old note

Sources:
Some source.

HSN-20260804-0900 --- Later snapshot --- 2026-08-04 09:00 EDT

Node status:
Kharg Island Export Terminal | Category 2 | Current status | Current note

Sources:
Another source.
"""

NO_CHANGE_NODE = """\
HSN-20260804-1000 --- Minor update --- 2026-08-04 10:00 EDT

Node status:
Kharg Island Export Terminal | Category 2 | No change
Jask Terminal | Category 4 | Still overloaded | Some note

Sources:
Sources here.
"""

EMPTY_NODE_SECTION = """\
HSN-20260804-1100 --- No nodes listed --- 2026-08-04 11:00 EDT

Node status:

Sources:
None.
"""

MISSING_NODE_SECTION = """\
HSN-20260804-1200 --- Missing node section --- 2026-08-04 12:00 EDT

Changes since last report:
Some change.

Sources:
Sources.
"""

ATX_PREFIXED = """\
# HSN-20260804-0900 --- ATX-prefixed heading --- 2026-08-04 09:00 EDT

# Node status:
# Kharg Island Export Terminal | Category 3 | Output trapped by insurance | None
# Jask Terminal | Category 4 | Bypass overloaded | Some note

# Sources:
"""

BOM_PREFIXED = (
    "﻿HSN-20260804-0900 --- BOM-prefixed report --- 2026-08-04 09:00 EDT\n\n"
    "Node status:\n"
    "Kharg Island Export Terminal | Category 1 | Destroyed | None\n"
)

EMPTY_DOC = "This document has no HSN reports at all."

# Delimiters with no surrounding spaces — must still parse.
UNSPACED_PIPES = """\
HSN-20260804-0900 --- Unspaced delimiters --- 2026-08-04 09:00 EDT

Node status:
Kharg Island Export Terminal|Category 2|Loading halted|Under review
Jask Terminal|Category 4|Overloaded|No bypass

Sources:
None.
"""

# A section header the old narrow NEXT_SECTION_RE could not match (digit +
# apostrophe), followed by pipe-bearing prose that must NOT become a node.
BLEEDING_SECTION = """\
HSN-20260804-0900 --- Section bleed --- 2026-08-04 09:00 EDT

Node status:
Kharg Island | Category 2 | Halted | None

Top 5 sources:
Lloyd List | Report 3 | Published today
"""

# Unparseable pipe-bearing lines inside the node section.
MALFORMED_NODE_LINES = """\
HSN-20260804-0900 --- Malformed lines --- 2026-08-04 09:00 EDT

Node status:
Good Node | Category 2 | Fine | Note
Truncated Node | Category 3
| Category 1 | no name | note

Sources:
None.
"""

INVALID_CATEGORY = """\
HSN-20260804-0900 --- Invalid category test --- 2026-08-04 09:00 EDT

Node status:
Bad Node | Category 9 | Some status | Some note
Good Node | Category 2 | Valid status | Valid note

Sources:
None.
"""


# ── _do_fetch_and_reparse(): session-state transitions via a fake `st` ────────

class FakeSessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class _NullSpinner:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSt:
    def __init__(self):
        self.session_state = FakeSessionState()

    def spinner(self, _text):
        return _NullSpinner()


def _snap(report_id: str, node_count: int) -> HsnSnapshot:
    return HsnSnapshot(
        report_id=report_id,
        summary="summary",
        timestamp_display="2026-08-04 09:00 EDT",
        nodes=[
            NodeEntry(name=f"Node {i}", category=1, status="s", note="n")
            for i in range(node_count)
        ],
    )


def test_do_fetch_and_reparse() -> None:
    fake_st = FakeSt()
    real_st = nsv.st
    real_fetch = nsv.fetch_hsn_text
    real_parse = nsv.parse_latest_snapshot
    nsv.st = fake_st
    try:
        good = _snap("20260804-0900", 4)
        nsv.fetch_hsn_text = lambda url=None, timeout=10.0: "irrelevant"

        # clean parse populates cache and is not stale
        nsv.parse_latest_snapshot = lambda text: good
        nsv._do_fetch_and_reparse()
        check("fetch: clean parse populates cache", fake_st.session_state.ns_cache is good)
        check("fetch: clean parse is not stale", fake_st.session_state.ns_stale is False)

        # a later zero-node parse must NOT clobber the good cache
        empty = _snap("20260805-0900", 0)
        nsv.parse_latest_snapshot = lambda text: empty
        nsv._do_fetch_and_reparse()
        check("fetch: zero-node parse preserves cache", fake_st.session_state.ns_cache is good)
        check("fetch: zero-node parse marks stale", fake_st.session_state.ns_stale is True)
        check(
            "fetch: zero-node parse explains itself",
            "0 nodes" in fake_st.session_state.ns_last_status,
        )

        # a later non-empty parse does replace the cache and clears stale
        newer = _snap("20260806-0900", 2)
        nsv.parse_latest_snapshot = lambda text: newer
        nsv._do_fetch_and_reparse()
        check("fetch: newer good parse replaces cache", fake_st.session_state.ns_cache is newer)
        check("fetch: newer good parse clears stale", fake_st.session_state.ns_stale is False)
        check("fetch: newer good parse clears status", fake_st.session_state.ns_last_status == "")

        # zero nodes with no prior cache IS shown (better than a blank view)
        fake_st.session_state.clear()
        nsv.parse_latest_snapshot = lambda text: empty
        nsv._do_fetch_and_reparse()
        check("fetch: zero nodes with no prior cache is cached", fake_st.session_state.ns_cache is empty)
        check("fetch: zero nodes with no prior cache is not stale", fake_st.session_state.ns_stale is False)

        # access error leaves an existing cache untouched
        fake_st.session_state.clear()
        fake_st.session_state.ns_cache = good
        fake_st.session_state.ns_stale = False

        def _boom(url=None, timeout=10.0):
            raise NodeStatusAccessError("HTTP 503")

        nsv.fetch_hsn_text = _boom
        nsv._do_fetch_and_reparse()
        check("fetch: access error leaves cache untouched", fake_st.session_state.ns_cache is good)
        check("fetch: access error marks stale", fake_st.session_state.ns_stale is True)
        check("fetch: access error status mentions access error", "access error" in fake_st.session_state.ns_last_status)

        # no headings at all leaves an existing cache untouched
        fake_st.session_state.clear()
        fake_st.session_state.ns_cache = good
        nsv.fetch_hsn_text = lambda url=None, timeout=10.0: "irrelevant"
        nsv.parse_latest_snapshot = lambda text: None
        nsv._do_fetch_and_reparse()
        check("fetch: no headings leaves cache untouched", fake_st.session_state.ns_cache is good)
        check("fetch: no headings marks stale", fake_st.session_state.ns_stale is True)
    finally:
        nsv.st = real_st
        nsv.fetch_hsn_text = real_fetch
        nsv.parse_latest_snapshot = real_parse


# ── tests ─────────────────────────────────────────────────────────────────────

def run() -> None:
    # single report: parses correctly
    snap = parse_latest_snapshot(SINGLE_REPORT)
    check("single report: snapshot not None", snap is not None)
    if snap:
        check("single report: report_id", snap.report_id == "20260804-0900")
        check("single report: summary", "Kharg export path" in snap.summary)
        check("single report: timestamp_display", snap.timestamp_display == "2026-08-04 09:00 EDT")
        check("single report: 4 nodes", len(snap.nodes) == 4)

        kharg = snap.nodes[0]
        check("node 0: name", kharg.name == "Kharg Island Export Terminal")
        check("node 0: category 2", kharg.category == 2)
        check("node 0: status non-empty", "loading halt" in kharg.status)
        check("node 0: note non-empty", "Drone-proof" in kharg.note)

        fujairah = snap.nodes[3]
        check("node 3: category 1", fujairah.category == 1)
        check("node 3: name", fujairah.name == "Fujairah Anchorage")

    # two reports: lex-latest is selected
    snap = parse_latest_snapshot(TWO_REPORTS)
    check("two reports: snapshot not None", snap is not None)
    if snap:
        check("two reports: latest report selected", snap.report_id == "20260804-0900")
        check("two reports: 1 node from latest", len(snap.nodes) == 1)
        check("two reports: correct node name", snap.nodes[0].name == "Kharg Island Export Terminal")

    # no-change node: 3 parts, note is empty
    snap = parse_latest_snapshot(NO_CHANGE_NODE)
    check("no-change: snapshot not None", snap is not None)
    if snap:
        check("no-change: 2 nodes", len(snap.nodes) == 2)
        nc = snap.nodes[0]
        check("no-change: status is 'No change'", nc.status == "No change")
        check("no-change: note is empty string", nc.note == "")
        check("no-change: node 2 has note", snap.nodes[1].note == "Some note")

    # empty node section: zero nodes, snapshot still returned
    snap = parse_latest_snapshot(EMPTY_NODE_SECTION)
    check("empty node section: snapshot not None", snap is not None)
    if snap:
        check("empty node section: 0 nodes", len(snap.nodes) == 0)

    # missing node section: snapshot returned with 0 nodes
    snap = parse_latest_snapshot(MISSING_NODE_SECTION)
    check("missing node section: snapshot not None", snap is not None)
    if snap:
        check("missing node section: 0 nodes", len(snap.nodes) == 0)

    # ATX-prefixed headings and labels
    snap = parse_latest_snapshot(ATX_PREFIXED)
    check("ATX-prefixed: snapshot not None", snap is not None)
    if snap:
        check("ATX-prefixed: 2 nodes", len(snap.nodes) == 2)
        check("ATX-prefixed: node 0 category 3", snap.nodes[0].category == 3)

    # BOM-prefixed document
    snap = parse_latest_snapshot(BOM_PREFIXED)
    check("BOM-prefixed: snapshot not None", snap is not None)
    if snap:
        check("BOM-prefixed: 1 node", len(snap.nodes) == 1)
        check("BOM-prefixed: category 1", snap.nodes[0].category == 1)

    # no headings at all → None
    snap = parse_latest_snapshot(EMPTY_DOC)
    check("no headings: returns None", snap is None)

    # invalid category (9) is skipped; valid category (2) kept
    snap = parse_latest_snapshot(INVALID_CATEGORY)
    check("invalid category: snapshot not None", snap is not None)
    if snap:
        check("invalid category: only 1 valid node kept", len(snap.nodes) == 1)
        check("invalid category: valid node name", snap.nodes[0].name == "Good Node")
        check("invalid category: valid category", snap.nodes[0].category == 2)

    # unspaced pipe delimiters still parse
    snap = parse_latest_snapshot(UNSPACED_PIPES)
    check("unspaced pipes: snapshot not None", snap is not None)
    if snap:
        check("unspaced pipes: 2 nodes", len(snap.nodes) == 2)
        check("unspaced pipes: name stripped", snap.nodes[0].name == "Kharg Island Export Terminal")
        check("unspaced pipes: category parsed", snap.nodes[0].category == 2)
        check("unspaced pipes: note parsed", snap.nodes[0].note == "Under review")
        check("unspaced pipes: no skipped lines", snap.skipped_lines == 0)

    # a digit/apostrophe-bearing section header still terminates the node section
    snap = parse_latest_snapshot(BLEEDING_SECTION)
    check("section bleed: snapshot not None", snap is not None)
    if snap:
        check("section bleed: only the real node kept", len(snap.nodes) == 1)
        check("section bleed: no phantom node", all(n.name != "Lloyd List" for n in snap.nodes))
        check("section bleed: nothing counted as skipped", snap.skipped_lines == 0)

    # malformed pipe-bearing lines are counted, not silently dropped
    snap = parse_latest_snapshot(MALFORMED_NODE_LINES)
    check("malformed lines: snapshot not None", snap is not None)
    if snap:
        check("malformed lines: 1 good node kept", len(snap.nodes) == 1)
        check("malformed lines: good node name", snap.nodes[0].name == "Good Node")
        check("malformed lines: 2 skipped counted", snap.skipped_lines == 2)

    # a well-formed report reports zero skipped lines
    snap = parse_latest_snapshot(SINGLE_REPORT)
    if snap:
        check("single report: skipped_lines is 0", snap.skipped_lines == 0)

    # ── category field is anchored, not "first digit anywhere" ──────────────
    from node_status_view import _parse_node_line
    check("category: 'Category 12' rejected", _parse_node_line("N | Category 12 | s | n") is None)
    check("category: bare '3' rejected", _parse_node_line("N | 3 | s | n") is None)
    check("category: 'Category 0' rejected", _parse_node_line("N | Category 0 | s | n") is None)
    check("category: 'Category 5' rejected", _parse_node_line("N | Category 5 | s | n") is None)
    check("category: 'category 2' accepted (case-insensitive)",
          _parse_node_line("N | category 2 | s | n").category == 2)
    check("category: 'Category  4' accepted (extra space)",
          _parse_node_line("N | Category  4 | s | n").category == 4)

    # ── report IDs must be YYYYMMDD-HHMM, so lex order == chronological ─────
    MIXED_IDS = (
        "HSN-20260804-0900 — real report — ts\n\n"
        "Node status:\nA | Category 1 | s | n\n\n"
        "HSN-DRAFT-test — draft heading — ts\n\n"
        "Node status:\nB | Category 2 | s | n\n"
    )
    snap = parse_latest_snapshot(MIXED_IDS)
    check("malformed id: real report still selected", snap.report_id == "20260804-0900")
    check("malformed id: draft heading ignored", snap.nodes[0].name == "A")

    # ── heading separator variants ──────────────────────────────────────────
    # The live doc uses an em dash; "---" was the original (broken) assumption.
    for label, sep in [("em dash", "—"), ("en dash", "–"), ("triple hyphen", "---")]:
        doc = (
            f"HSN-20260804-0900 {sep} Summary text {sep} 2026-08-04 09:00 EDT\n\n"
            "Node status:\n"
            "Kharg Island | Category 2 | Halted | None\n"
        )
        snap = parse_latest_snapshot(doc)
        check(f"separator {label}: parses", snap is not None)
        if snap:
            check(f"separator {label}: summary", snap.summary == "Summary text")
            check(f"separator {label}: timestamp", snap.timestamp_display == "2026-08-04 09:00 EDT")
            check(f"separator {label}: 1 node", len(snap.nodes) == 1)

    # a summary containing the separator keeps its full text
    snap = parse_latest_snapshot(
        "HSN-20260804-0900 — Kharg — Jask corridor — 2026-08-04 09:00 EDT\n\n"
        "Node status:\nA | Category 1 | s | n\n"
    )
    check("multi-separator heading: full summary kept", snap.summary == "Kharg — Jask corridor")
    check("multi-separator heading: timestamp is last field",
          snap.timestamp_display == "2026-08-04 09:00 EDT")

    # ── duplicate report IDs ────────────────────────────────────────────────
    DUP = (
        "HSN-20260804-0900 — First copy — ts\n\n"
        "Node status:\nA | Category 1 | from first | n\n\n"
        "HSN-20260804-0900 — Second copy — ts\n\n"
        "Node status:\nB | Category 2 | from second | n\n"
    )
    snap = parse_latest_snapshot(DUP)
    check("duplicate id: last copy wins", snap.summary == "Second copy")
    check("duplicate id: nodes from last copy", snap.nodes[0].name == "B")
    check("duplicate id: duplicate_copies counted", snap.duplicate_copies == 1)
    check("single report: duplicate_copies is 0",
          parse_latest_snapshot(SINGLE_REPORT).duplicate_copies == 0)

    # ── card rendering escapes attacker-influenceable text ──────────────────
    evil = NodeEntry(
        name='<img src=x onerror=alert(1)>',
        category=2,
        status='" onmouseover="alert(2)',
        note="<script>alert(3)</script>",
    )
    card = _render_node_card(evil)
    check("render: no raw <img", "<img" not in card)
    check("render: no raw <script", "<script" not in card)
    check("render: name escaped", "&lt;img src=x" in card)
    check("render: status escaped", "&quot;" in card)
    check("render: note escaped", "&lt;script&gt;" in card)
    check("render: badge colour is from the internal map",
          'style="background:#c2410c"' in card)

    # ── live-document compatibility baseline ────────────────────────────────
    # Frozen export of the real HSN doc. Guards against the failure this parser
    # actually shipped with: a heading separator that matched nothing real.
    with open(LIVE_FIXTURE, encoding="utf-8") as fh:
        live = fh.read()
    snap = parse_latest_snapshot(live)
    check("live doc: parses", snap is not None)
    if snap:
        check("live doc: latest report selected", snap.report_id == "20260804-0803")
        check("live doc: summary non-empty and untruncated",
              snap.summary == "No category transitions; new Hormuz strike keeps traffic impaired")
        check("live doc: timestamp", snap.timestamp_display == "Aug 4, 2026, 8:03 AM EDT")
        check("live doc: 17 nodes", len(snap.nodes) == 17)
        check("live doc: no skipped lines", snap.skipped_lines == 0)
        check("live doc: no duplicate ids", snap.duplicate_copies == 0)
        check("live doc: every category in 1-4",
              all(n.category in (1, 2, 3, 4) for n in snap.nodes))
        check("live doc: every node has a name", all(n.name for n in snap.nodes))
        check("live doc: apostrophe in node name survives",
              any("Ju'aymah" in n.name for n in snap.nodes))
        check("live doc: en-dash in node name survives",
              any("East–West" in n.name for n in snap.nodes))
        check("live doc: colon-prefixed node names survive",
              any(n.name.startswith("Kuwait:") for n in snap.nodes))
        check("live doc: 'No change' rows parsed with empty note",
              any(n.status == "No change" and n.note == "" for n in snap.nodes))
        check("live doc: full 4-field row keeps its note",
              any(n.name == "Strait of Hormuz" and n.note for n in snap.nodes))
        check("live doc: no 'Sources' bleed into nodes",
              all("Sources" not in n.name for n in snap.nodes))

    # ── timestamp → UTC instant ─────────────────────────────────────────────
    def utc(s):
        return datetime(*s, tzinfo=timezone.utc) if isinstance(s, tuple) else s

    def ts(display, rid="20260804-0803"):
        return parse_report_timestamp(display, rid)

    # the format the live doc actually uses
    check("ts: live format", ts("Aug 4, 2026, 8:03 AM EDT") == utc((2026, 8, 4, 12, 3)))
    check("ts: EST is -5", ts("January 15, 2026, 11:30 PM EST", "20260115-2330")
          == utc((2026, 1, 16, 4, 30)))
    check("ts: noon PM not 00", ts("Aug 4, 2026, 12:00 PM EDT") == utc((2026, 8, 4, 16, 0)))
    check("ts: midnight AM not 12", ts("Aug 4, 2026, 12:00 AM EDT") == utc((2026, 8, 4, 4, 0)))
    check("ts: ISO-ish with zone", ts("2026-08-04 09:00 EDT") == utc((2026, 8, 4, 13, 0)))
    check("ts: ISO with T", ts("2026-08-04T09:00") == utc((2026, 8, 4, 13, 0)))
    check("ts: 24-hour clock", ts("Aug 4, 2026, 13:45", "20260804-1345") == utc((2026, 8, 4, 17, 45)))
    check("ts: day-first", ts("4 Aug 2026, 8:03 AM UTC") == utc((2026, 8, 4, 8, 3)))
    check("ts: numeric offset", ts("Aug 4, 2026, 8:03 AM +03:00") == utc((2026, 8, 4, 5, 3)))
    check("ts: UTC-n offset", ts("Aug 4, 2026, 8:03 AM UTC-4") == utc((2026, 8, 4, 12, 3)))
    check("ts: GMT is zero", ts("Aug 4, 2026, 8:03 AM GMT") == utc((2026, 8, 4, 8, 3)))
    check("ts: trailing period tolerated", ts("Aug 4, 2026, 8:03 AM EDT.") == utc((2026, 8, 4, 12, 3)))

    # DST is applied from the source zone when the doc names no zone
    check("ts: no zone, winter -> EST", ts("2026-01-15 09:00", "20260115-0900") == utc((2026, 1, 15, 14, 0)))
    check("ts: no zone, summer -> EDT", ts("2026-07-15 09:00", "20260715-0900") == utc((2026, 7, 15, 13, 0)))

    # ambiguous abbreviations must never be guessed
    for amb in ("AST", "CST", "IST", "GST", "CDT", "BST"):
        check(f"ts: ambiguous {amb} refused",
              ts(f"Aug 4, 2026, 8:03 AM {amb}") is None)

    # fallback to the report ID when the heading is unparseable but uncontested
    check("ts: falls back to report id", ts("sometime yesterday") == utc((2026, 8, 4, 12, 3)))
    check("ts: no id, no heading -> None", parse_report_timestamp("nonsense", "notanid") is None)
    check("ts: empty input -> None", parse_report_timestamp("", "") is None)
    check("ts: impossible id date -> None", parse_report_timestamp("x", "20260230-9999") is None)

    # an unknown zone must NOT silently fall through to the report-ID guess
    check("ts: unknown zone blocks id fallback",
          parse_report_timestamp("Aug 4, 2026, 8:03 AM GST", "20260804-0803") is None)

    # source zone is configurable
    _prev = os.environ.get("HSN_SOURCE_TZ")
    os.environ["HSN_SOURCE_TZ"] = "Asia/Dubai"
    check("ts: HSN_SOURCE_TZ honoured", ts("2026-08-04 09:00", "20260804-0900") == utc((2026, 8, 4, 5, 0)))
    os.environ["HSN_SOURCE_TZ"] = "Not/AZone"
    check("ts: bad HSN_SOURCE_TZ degrades to UTC, no crash",
          ts("2026-08-04 09:00", "20260804-0900") == utc((2026, 8, 4, 9, 0)))
    if _prev is None:
        os.environ.pop("HSN_SOURCE_TZ", None)
    else:
        os.environ["HSN_SOURCE_TZ"] = _prev

    # every heading in the live document yields an instant
    with open(LIVE_FIXTURE, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("HSN-"):
                head = line.strip()
                rid = head[4:17]
                disp = head.rsplit("—", 1)[-1].strip()
                check(f"ts: live heading {rid} resolves",
                      parse_report_timestamp(disp, rid) is not None)

    # ── fetch_hsn_text: stubbed requests.get, no network calls ───────────────

    class _FakeResp:
        def __init__(self, status_code=200, url="https://docs.google.com/document/d/x/export?format=txt",
                     headers=None, content=b""):
            self.status_code = status_code
            self.url = url
            # `headers or {...}` would coalesce an intentionally-empty {} back
            # to the default, making the missing-header case untestable.
            self.headers = (
                headers if headers is not None
                else {"content-type": "text/plain; charset=UTF-8"}
            )
            self.content = content

    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(content="HSN-20260804-0900 --- summary --- ts\n".encode("utf-8"))
        text = fetch_hsn_text()
        check("fetch_hsn_text: happy path returns text", "HSN-20260804-0900" in text)

    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(status_code=404)
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: non-200 raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: non-200 raises", "404" in str(e))

    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(
            status_code=200,
            url="https://accounts.google.com/ServiceLogin",
            headers={"content-type": "text/html; charset=UTF-8"},
            content=b"<html>login</html>",
        )
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: login-wall HTML raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: login-wall HTML raises", "accounts.google.com" in str(e))

    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(content=b"")
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: empty response raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: empty response raises", "empty" in str(e))

    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(content=b"No reports here.")
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: text without HSN heading raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: text without HSN heading raises", "no HSN" in str(e))

    test_do_fetch_and_reparse()

    # network-layer exception is wrapped, not propagated raw
    with mock.patch("node_status_view.requests.get") as m:
        m.side_effect = nsv.requests.exceptions.ConnectTimeout("timed out")
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: RequestException wrapped", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: RequestException wrapped", "request failed" in str(e))
        except nsv.requests.exceptions.RequestException:
            check("fetch_hsn_text: RequestException wrapped", False)

    # a 200 that isn't text/plain and isn't html (e.g. a PDF/JSON export)
    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(headers={"content-type": "application/pdf"},
                                   content=b"%PDF-1.4")
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: unexpected content type raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: unexpected content type raises",
                  "unexpected content type" in str(e))

    # missing content-type header entirely
    with mock.patch("node_status_view.requests.get") as m:
        m.return_value = _FakeResp(headers={}, content=b"whatever")
        try:
            fetch_hsn_text()
            check("fetch_hsn_text: missing content type raises", False)
        except NodeStatusAccessError as e:
            check("fetch_hsn_text: missing content type raises", "missing" in str(e))

    print()
    print(f"{_total - _failures}/{_total} passed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
