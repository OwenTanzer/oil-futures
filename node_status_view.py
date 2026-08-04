"""
Location Status Monitor — renders the most recent Hormuz Node Status (HSN) snapshot.
Parser and Streamlit view in one module; no dependency on breaking_news.py.
Invoked by mediaflow_app.py when session_state.mode == "node_status".
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

import requests
import streamlit as st
import streamlit.components.v1 as components

from tz_convert import inject_converter, parse_doc_timestamp, resolve_source_tz

POLL_INTERVAL_SECONDS = 300

DEFAULT_HSN_DOC_ID = "1bZUorg8zDfNBY9sMi9T7OFjY7ScMLAtSkEFuXFNqADs"


def build_export_url(doc_id: str = DEFAULT_HSN_DOC_ID) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=txt"


# Same override convention as breaking_news_view.py: point at a different
# source doc without a code change/redeploy.
HSN_DOC_ID = os.environ.get("HSN_DOC_ID", DEFAULT_HSN_DOC_ID)
HSN_EXPORT_URL = os.environ.get("HSN_EXPORT_URL") or build_export_url(HSN_DOC_ID)

ATX_PREFIX_RE = re.compile(r"(?m)^#{1,6}[ \t]*")

# HSN-YYYYMMDD-HHMM <sep> summary <sep> timestamp
#
# The live doc uses an em dash (Google Docs autocorrects "---" as you type, and
# breaking_news.py's HFW parser hit the same thing). Accepting em dash, en dash
# and the literal "---" means an author toggling autocorrect off can't silently
# break the whole view — the previous "---"-only pattern matched nothing at all
# against the real document, so the view showed "no HSN reports found".
SEP = r"(?:---|—|–)"

# The summary group is non-greedy and the timestamp is anchored to the LAST
# separator on the line, so a summary that itself contains a dash separator
# ("Kharg — Jask corridor — <ts>") keeps the full summary instead of splitting
# at the first one and letting the timestamp swallow the remainder.
# The ID is constrained to YYYYMMDD-HHMM (the format every report in the live
# doc uses) rather than a loose [\w-]+. That is what makes the lexicographic
# "latest" sort below equivalent to a chronological one — with the loose
# pattern a stray "HSN-DRAFT-…" heading sorts above every real report and
# hijacks the view.
HEADING_RE = re.compile(
    rf"(?m)^HSN-(\d{{8}}-\d{{4}})\s*{SEP}\s*(.+)\s*{SEP}\s*(\S[^\n]*?)\s*$"
)

# "Node status:" section header (case-insensitive, possibly prefixed by ATX #)
NODE_SECTION_RE = re.compile(r"(?im)^Node status:\s*")

# Next section header in the block (ends the Node status: section). Character
# class and 80-char bound match breaking_news.py's LABEL_RE for the same reason:
# a narrower class silently fails to terminate the section on real headers like
# "Top 5 sources:" or "Lloyd's List:", letting downstream pipe-bearing lines be
# ingested as phantom nodes. A node line can never match this — "|" is not in
# the class.
NEXT_SECTION_RE = re.compile(r"(?m)^[A-Z][A-Za-z0-9()&./' -]{0,79}:\s*$")

# Field 2 of a node line, e.g. "Category 3".
CATEGORY_FIELD_RE = re.compile(r"(?i)^Category\s+([1-4])$")


# ── timestamps ────────────────────────────────────────────────────────────────
# The parsing itself lives in tz_convert.py — both this view and Breaking News
# need it, and it is the server half of the same mechanism as the browser-side
# converter. This wrapper only binds the HSN-specific source-zone override.

def parse_report_timestamp(timestamp_display: str, report_id: str = "") -> datetime | None:
    """Best-effort UTC instant for an HSN heading, or None if not confident."""
    return parse_doc_timestamp(
        timestamp_display, report_id, resolve_source_tz("HSN_SOURCE_TZ")
    )



# Badge text is white at ~11px, i.e. "small text" for WCAG purposes, so each
# colour must clear 4.5:1 against white. The original ramp did not: #ea580c was
# 3.56:1 and #d97706 was 3.19:1. These are the same hues one step darker —
# measured 6.47 / 5.18 / 5.02 / 7.10:1 respectively.
CATEGORY_COLORS = {
    1: "#b91c1c",  # red
    2: "#c2410c",  # orange
    3: "#b45309",  # amber
    4: "#6d28d9",  # purple
}


# ── data model ────────────────────────────────────────────────────────────────

@dataclass
class NodeEntry:
    name: str
    category: int          # 1–4
    status: str
    note: str = ""         # bypass/restoration note; empty when absent


@dataclass
class HsnSnapshot:
    report_id: str
    summary: str
    timestamp_display: str
    nodes: list[NodeEntry] = field(default_factory=list)
    # Pipe-bearing lines inside "Node status:" that failed to parse. Surfaced in
    # the view rather than dropped silently — on a monitoring instrument, a
    # node that vanishes because of source drift must not look like a node that
    # simply isn't there.
    skipped_lines: int = 0
    # Extra copies of this report's ID found in the doc. Non-zero means the
    # report was re-issued; the last copy is the one rendered.
    duplicate_copies: int = 0


class NodeStatusAccessError(Exception):
    """Raised when the HSN document can't be fetched (not a parse issue)."""


# ── parser ────────────────────────────────────────────────────────────────────

def fetch_hsn_text(url: str = HSN_EXPORT_URL, timeout: float = 10.0) -> str:
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        raise NodeStatusAccessError(f"request failed: {e}") from e

    if resp.status_code != 200:
        raise NodeStatusAccessError(f"HTTP {resp.status_code}")

    content_type = resp.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        host = urlparse(resp.url).netloc
        raise NodeStatusAccessError(
            f"received HTML from {host} instead of plain-text export "
            "(document may not be public)"
        )
    if "text/plain" not in content_type:
        raise NodeStatusAccessError(
            f"unexpected content type: {content_type or 'missing'}"
        )

    text = resp.content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise NodeStatusAccessError("received an empty plain-text export")
    if not re.search(r"(?m)^#{0,6}[ \t]*HSN-", text):
        raise NodeStatusAccessError(
            "plain-text export contains no HSN report headings"
        )
    return text


def _parse_node_line(line: str) -> NodeEntry | None:
    # Split on the bare pipe, not " | " — the doc's spacing around delimiters is
    # not guaranteed, and splitting on the spaced form drops every node in an
    # unspaced document while still rendering a normal-looking (empty) page.
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    name = parts[0]
    if not name:
        return None
    # Anchored, not "first digit anywhere in the field". The loose form read
    # "Category 12" as Category 1 and accepted a bare "3" — both silently wrong
    # rather than skipped. Every row in the live doc is exactly "Category N".
    m = CATEGORY_FIELD_RE.match(parts[1])
    if not m:
        return None
    category = int(m.group(1))
    status = parts[2]
    note = parts[3] if len(parts) >= 4 else ""
    return NodeEntry(name=name, category=category, status=status, note=note)


def parse_latest_snapshot(text: str) -> HsnSnapshot | None:
    """Return the most recent HSN snapshot, or None if no headings found."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    text = ATX_PREFIX_RE.sub("", text)

    headings = list(HEADING_RE.finditer(text))
    if not headings:
        return None

    # Pick the most recent snapshot by ID. Report IDs are YYYYMMDD-HHMM, so lex
    # order is chronological order. Sorting (id, doc_index) descending means
    # that when the same ID appears twice — the natural way a correction gets
    # made to a hand-maintained doc — the LAST occurrence wins, so a re-issued
    # report supersedes the copy it was meant to replace rather than being
    # silently discarded in its favour. The duplicate is still reported.
    indexed = list(enumerate(headings))
    doc_idx, h = max(indexed, key=lambda pair: (pair[1].group(1), pair[0]))

    report_id = h.group(1)
    # group(2) is greedy up to the last separator, so any whitespace sitting
    # before that separator stays in the capture — strip it here rather than in
    # the pattern, which would reintroduce the first-separator split.
    summary = h.group(2).strip()
    timestamp_display = h.group(3).strip()

    duplicate_of_latest = sum(1 for x in headings if x.group(1) == report_id) - 1

    # Slice the block between this heading and the next one in document order
    block_start = h.end()
    block_end = headings[doc_idx + 1].start() if doc_idx + 1 < len(headings) else len(text)
    block = text[block_start:block_end]

    # Find "Node status:" section within the block
    ns_match = NODE_SECTION_RE.search(block)
    if not ns_match:
        return HsnSnapshot(
            report_id=report_id,
            summary=summary,
            timestamp_display=timestamp_display,
            duplicate_copies=duplicate_of_latest,
        )

    ns_body_start = ns_match.end()

    # End at the next section header inside the block
    next_sec = NEXT_SECTION_RE.search(block, ns_body_start)
    ns_body_end = next_sec.start() if next_sec else len(block)

    node_text = block[ns_body_start:ns_body_end]

    nodes: list[NodeEntry] = []
    skipped = 0
    for line in node_text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        entry = _parse_node_line(line)
        if entry is not None:
            nodes.append(entry)
        else:
            skipped += 1

    return HsnSnapshot(
        report_id=report_id,
        summary=summary,
        timestamp_display=timestamp_display,
        nodes=nodes,
        skipped_lines=skipped,
        duplicate_copies=duplicate_of_latest,
    )


# ── Streamlit view ────────────────────────────────────────────────────────────

NODE_STATUS_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Crimson+Text:ital,wght@0,400;0,600;1,400&family=Oxanium:wght@700&display=swap');
:root { color-scheme: light; }
[data-testid="stAppViewContainer"] { background: #fff; }
[data-testid="stSidebar"]          { display: none; }
[data-testid="collapsedControl"]   { display: none; }
[data-testid="stHeader"]           { display: none; }
[data-testid="stToolbar"]          { display: none; }
.block-container { padding-top: 0.6rem !important; padding-bottom: 1rem !important; }
body, p, div, span, .stMarkdown {
    font-family: 'Crimson Text', Georgia, serif !important;
}
div[data-testid="stButton"] > button,
div[data-testid="stButton"] > button > div,
div[data-testid="stButton"] > button p {
    font-family: 'Oxanium', monospace !important;
    font-weight: 700 !important;
}
.ns-meta {
    font-family: 'Oxanium', monospace;
    font-size: 0.72em;
    color: #6b7280;
    font-weight: 700;
    letter-spacing: 0.04em;
    margin: 2px 0 10px;
}
.ns-grid {
    display: grid;
    /* min() keeps the track from forcing horizontal overflow below 290px */
    grid-template-columns: repeat(auto-fill, minmax(min(290px, 100%), 1fr));
    gap: 10px;
    padding: 2px 0 8px;
}
.ns-card {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 11px 14px 10px;
    background: #fafafa;
}
.ns-node-name {
    font-family: 'Crimson Text', Georgia, serif;
    font-weight: 600;
    font-size: 1.08em;
    margin: 0 0 5px;
    line-height: 1.25;
}
.ns-badge {
    display: inline-block;
    border-radius: 11px;
    padding: 2px 9px;
    font-family: 'Oxanium', monospace;
    font-weight: 700;
    font-size: 0.68em;
    color: #fff;
    margin-bottom: 7px;
    letter-spacing: 0.03em;
}
.ns-status {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 0.98em;
    line-height: 1.45;
    margin: 0 0 4px;
    color: #111;
}
.ns-note {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 0.88em;
    color: #57534e;
    font-style: italic;
    margin: 0;
    line-height: 1.4;
}
.ns-no-change {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 0.92em;
    color: #6b7280;
    margin: 0;
}
</style>
"""


def _inject_node_status_js() -> None:
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;

            // The view is gone once its back button leaves the DOM. Without
            // this check the keydown listener and the MutationObserver below
            // survive navigation for the rest of the session — and the
            // observer re-scans every iframe on every DOM mutation, on a
            // dashboard whose feed mutates constantly.
            function stillMounted() {
                return !!doc.querySelector('.st-key-ns_back');
            }
            function teardown() {
                doc.removeEventListener('keydown', fireBack);
                doc.__ns_esc__ = null;
                if (doc.__ns_obs__) {
                    try { doc.__ns_obs__.disconnect(); } catch (ignore) {}
                    doc.__ns_obs__ = null;
                }
            }

            function fireBack(e) {
                if (e.key !== 'Escape') return;
                var btn = doc.querySelector('.st-key-ns_back button');
                if (btn) { btn.click(); } else { teardown(); }
            }
            if (doc.__ns_esc__) doc.removeEventListener('keydown', doc.__ns_esc__);
            doc.__ns_esc__ = fireBack;
            doc.addEventListener('keydown', fireBack);

            function attachToIframes() {
                if (!stillMounted()) { teardown(); return; }
                doc.querySelectorAll('iframe').forEach(function(f) {
                    try {
                        if (!f.__ns_attached__) {
                            f.__ns_attached__ = true;
                            f.contentDocument.addEventListener('keydown', fireBack);
                        }
                    } catch (ignore) {}
                });
            }
            attachToIframes();
            if (doc.__ns_obs__) { try { doc.__ns_obs__.disconnect(); } catch(_) {} }
            doc.__ns_obs__ = new MutationObserver(attachToIframes);
            doc.__ns_obs__.observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=1,
    )


def _do_fetch_and_reparse() -> None:
    """Fetch + parse the HSN doc, updating session cache. Never raises.

    A source-format change must never clobber an already-good cache. A snapshot
    that parsed to zero nodes is indistinguishable at the type level from a
    genuinely empty report, so it is only allowed to replace a cache that has
    nodes in it when the source really did go empty — which we cannot tell
    apart from a renamed "Node status:" heading. We therefore keep the last
    good data and flag it stale, mirroring breaking_news_view's behaviour.
    """
    try:
        # requests.get blocks the render thread for up to `timeout` seconds, so
        # without this the page paints its header and then silently stalls.
        with st.spinner("Fetching node status…"):
            text = fetch_hsn_text(HSN_EXPORT_URL)
        snapshot = parse_latest_snapshot(text)
    except NodeStatusAccessError as e:
        st.session_state.ns_stale = True
        st.session_state.ns_last_status = f"access error: {e}"
        st.session_state.ns_last_fetch = time.monotonic()
        return

    if snapshot is None:
        st.session_state.ns_stale = True
        st.session_state.ns_last_status = "no HSN reports found in source document"
        st.session_state.ns_last_fetch = time.monotonic()
        return

    cached: HsnSnapshot | None = st.session_state.get("ns_cache")
    if not snapshot.nodes and cached is not None and cached.nodes:
        # Zero nodes where we previously had some: almost certainly source
        # drift (renamed heading, changed delimiter), not a real empty report.
        st.session_state.ns_stale = True
        st.session_state.ns_last_status = (
            f"report HSN-{snapshot.report_id} parsed to 0 nodes; showing last good data"
        )
        st.session_state.ns_last_fetch = time.monotonic()
        return

    st.session_state.ns_cache = snapshot
    st.session_state.ns_stale = False
    st.session_state.ns_last_status = ""
    st.session_state.ns_last_fetch = time.monotonic()


def _render_node_card(node: NodeEntry) -> str:
    color = CATEGORY_COLORS.get(node.category, "#6b7280")
    badge_label = f"Category {node.category}"

    name_html = html.escape(node.name)
    badge_html = (
        f'<span class="ns-badge" style="background:{color}">'
        f'{html.escape(badge_label)}</span>'
    )
    status_html = (
        f'<p class="ns-no-change">{html.escape(node.status)}</p>'
        if node.status == "No change"
        else f'<p class="ns-status">{html.escape(node.status)}</p>'
    )
    note_html = (
        f'<p class="ns-note">{html.escape(node.note)}</p>'
        if node.note
        else ""
    )

    return (
        f'<div class="ns-card">'
        f'<p class="ns-node-name">{name_html}</p>'
        f'{badge_html}'
        f'{status_html}'
        f'{note_html}'
        f'</div>'
    )


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def _node_status_body() -> None:
    now = time.monotonic()
    last = st.session_state.get("ns_last_fetch")
    # run_every fires ~POLL_INTERVAL after the previous run *began*, but
    # ns_last_fetch is stamped after the fetch *completes*, so elapsed time is
    # always a fraction under the interval and a strict >= comparison skips
    # every other cycle — halving the real refresh rate to 10 minutes. The
    # tolerance absorbs that drift. (breaking_news_view.py has the same bug at
    # 60s; left alone here to keep this PR scoped to the new feature.)
    if last is None or now - last >= POLL_INTERVAL_SECONDS - 5:
        _do_fetch_and_reparse()

    snapshot: HsnSnapshot | None = st.session_state.get("ns_cache")

    if snapshot is None:
        status = st.session_state.get("ns_last_status")
        if status:
            st.error(f"No node status available: {status}")
        else:
            st.info("No node status available yet.")
        return

    if st.session_state.get("ns_stale"):
        st.caption(
            f"⚠ showing cached data — "
            f"{st.session_state.get('ns_last_status', 'last refresh failed')}"
        )

    # The doc's own string is always what's rendered; when we have a confident
    # instant it also carries data-utc, and the browser rewrites it in place to
    # the viewer's zone. No JS, or an instant we couldn't derive → the author's
    # timezone shows through unchanged, which is correct, just not local.
    instant = parse_report_timestamp(snapshot.timestamp_display, snapshot.report_id)
    ts_html = html.escape(snapshot.timestamp_display)
    if instant is not None:
        ts_html = (
            f'<span data-utc="{html.escape(instant.isoformat(), quote=True)}">'
            f'{ts_html}</span>'
        )
    meta_line = f'{html.escape(f"HSN-{snapshot.report_id}")}  ·  {ts_html}'
    summary_line = html.escape(snapshot.summary)
    st.markdown(
        f'<div class="ns-meta">{meta_line}</div>'
        f'<p style="font-family:\'Crimson Text\',serif;font-weight:600;'
        f'font-size:1.18em;margin:0 0 10px;line-height:1.3">{summary_line}</p>',
        unsafe_allow_html=True,
    )

    if snapshot.duplicate_copies:
        st.caption(
            f"⚠ HSN-{snapshot.report_id} appears "
            f"{snapshot.duplicate_copies + 1} times in the source document; "
            "showing the last copy"
        )

    if snapshot.skipped_lines:
        st.caption(
            f"⚠ {snapshot.skipped_lines} line(s) in the Node status section "
            "could not be parsed and are not shown"
        )

    if not snapshot.nodes:
        st.info("No node entries found in the most recent HSN report.")
        return

    cards_html = "".join(_render_node_card(n) for n in snapshot.nodes)
    st.markdown(
        f'<div class="ns-grid">{cards_html}</div>',
        unsafe_allow_html=True,
    )


def render_node_status() -> None:
    st.markdown(NODE_STATUS_CSS, unsafe_allow_html=True)
    _inject_node_status_js()
    # Converter only — the newscenter's 2-minute page reload must not follow the
    # user in here; this view refreshes itself via its own fragment poll.
    inject_converter()

    col_back, col_title = st.columns([1, 9])
    with col_back:
        if st.button("← Back", key="ns_back"):
            st.session_state.mode = "newscenter"
            st.rerun()
    with col_title:
        st.markdown(
            "<p style='font-family:\"Oxanium\",monospace;font-weight:700;font-size:1.1em;"
            "color:#999;padding-top:6px;margin:0;letter-spacing:0.06em'>NODE STATUS</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='margin:6px 0 10px'>", unsafe_allow_html=True)

    _node_status_body()
