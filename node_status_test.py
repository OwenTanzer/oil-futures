"""
Plain-assert tests for the HSN parser in node_status_view.py.
No pytest, no test framework dependency. Run with: python node_status_test.py
"""

from __future__ import annotations

import sys
from unittest import mock

from node_status_view import (
    NodeStatusAccessError,
    fetch_hsn_text,
    parse_latest_snapshot,
)

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

INVALID_CATEGORY = """\
HSN-20260804-0900 --- Invalid category test --- 2026-08-04 09:00 EDT

Node status:
Bad Node | Category 9 | Some status | Some note
Good Node | Category 2 | Valid status | Valid note

Sources:
None.
"""


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

    # ── fetch_hsn_text: stubbed requests.get, no network calls ───────────────

    class _FakeResp:
        def __init__(self, status_code=200, url="https://docs.google.com/document/d/x/export?format=txt",
                     headers=None, content=b""):
            self.status_code = status_code
            self.url = url
            self.headers = headers or {"content-type": "text/plain; charset=UTF-8"}
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

    print()
    print(f"{_total - _failures}/{_total} passed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
