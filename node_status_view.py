"""
Location Status Monitor — renders the most recent Hormuz Node Status (HSN) snapshot.
Parser and Streamlit view in one module; no dependency on breaking_news.py.
Invoked by mediaflow_app.py when session_state.mode == "node_status".
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
import streamlit as st
import streamlit.components.v1 as components

POLL_INTERVAL_SECONDS = 300

HSN_DOC_ID = "1bZUorg8zDfNBY9sMi9T7OFjY7ScMLAtSkEFuXFNqADs"
HSN_EXPORT_URL = f"https://docs.google.com/document/d/{HSN_DOC_ID}/export?format=txt"

ATX_PREFIX_RE = re.compile(r"(?m)^#{1,6}[ \t]*")

# HSN-YYYYMMDD-HHMM --- summary --- timestamp
HEADING_RE = re.compile(
    r"(?m)^HSN-([\w-]+)\s*---\s*(.+?)\s*---\s*(\S[^\n]*?)\s*$"
)

# "Node status:" section header (case-insensitive, possibly prefixed by ATX #)
NODE_SECTION_RE = re.compile(r"(?im)^Node status:\s*")

# Next section header in the block (ends the Node status: section)
NEXT_SECTION_RE = re.compile(r"(?m)^[A-Z][A-Za-z /()-]+:\s*$")

CATEGORY_COLORS = {
    1: "#dc2626",  # red
    2: "#ea580c",  # orange
    3: "#d97706",  # amber
    4: "#7c3aed",  # purple
}

CATEGORY_LABELS = {
    1: "Category 1",
    2: "Category 2",
    3: "Category 3",
    4: "Category 4",
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
    parts = [p.strip() for p in line.split(" | ")]
    if len(parts) < 3:
        return None
    name = parts[0]
    if not name:
        return None
    m = re.search(r"(\d)", parts[1])
    if not m:
        return None
    category = int(m.group(1))
    if category not in (1, 2, 3, 4):
        return None
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

    # Lex-sort descending to find the most recent snapshot by ID
    headings_by_id = sorted(headings, key=lambda h: h.group(1), reverse=True)
    h = headings_by_id[0]

    report_id = h.group(1)
    summary = h.group(2)
    timestamp_display = h.group(3)

    # Slice the block between this heading and the next one in document order
    doc_idx = next(i for i, x in enumerate(headings) if x is h)
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
        )

    ns_body_start = ns_match.end()

    # End at the next section header inside the block
    next_sec = NEXT_SECTION_RE.search(block, ns_body_start)
    ns_body_end = next_sec.start() if next_sec else len(block)

    node_text = block[ns_body_start:ns_body_end]

    nodes: list[NodeEntry] = []
    for line in node_text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        entry = _parse_node_line(line)
        if entry is not None:
            nodes.append(entry)

    return HsnSnapshot(
        report_id=report_id,
        summary=summary,
        timestamp_display=timestamp_display,
        nodes=nodes,
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
    color: #999;
    font-weight: 700;
    letter-spacing: 0.04em;
    margin: 2px 0 10px;
}
.ns-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
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
    color: #666;
    font-style: italic;
    margin: 0;
    line-height: 1.4;
}
.ns-no-change {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 0.92em;
    color: #aaa;
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
            function fireBack(e) {
                if (e.key !== 'Escape') return;
                var btn = doc.querySelector('.st-key-ns_back button');
                if (btn) btn.click();
            }
            if (doc.__ns_esc__) doc.removeEventListener('keydown', doc.__ns_esc__);
            doc.__ns_esc__ = fireBack;
            doc.addEventListener('keydown', fireBack);

            function attachToIframes() {
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
    try:
        text = fetch_hsn_text()
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

    st.session_state.ns_cache = snapshot
    st.session_state.ns_stale = False
    st.session_state.ns_last_status = ""
    st.session_state.ns_last_fetch = time.monotonic()


def _render_node_card(node: NodeEntry) -> str:
    color = CATEGORY_COLORS.get(node.category, "#6b7280")
    badge_label = CATEGORY_LABELS.get(node.category, f"Category {node.category}")

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
    if last is None or now - last >= POLL_INTERVAL_SECONDS:
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

    meta_line = html.escape(f"HSN-{snapshot.report_id}  ·  {snapshot.timestamp_display}")
    summary_line = html.escape(snapshot.summary)
    st.markdown(
        f'<div class="ns-meta">{meta_line}</div>'
        f'<p style="font-family:\'Crimson Text\',serif;font-weight:600;'
        f'font-size:1.18em;margin:0 0 10px;line-height:1.3">{summary_line}</p>',
        unsafe_allow_html=True,
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
