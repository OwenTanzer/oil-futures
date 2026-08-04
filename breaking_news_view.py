"""
Breaking News dashboard view — renders Hormuz Flow Watch reports.
Invoked by mediaflow_app.py when session_state.mode == "breaking_news".
"""

from __future__ import annotations

import html
import os
import time

import streamlit as st
import streamlit.components.v1 as components

from breaking_news import (
    BreakingNewsAccessError,
    DEFAULT_DOC_ID,
    build_export_url,
    fetch_doc_text,
    parse_reports,
)

POLL_INTERVAL_SECONDS = 60

DOC_ID = os.environ.get("BREAKING_NEWS_DOC_ID", DEFAULT_DOC_ID)
EXPORT_URL = os.environ.get("BREAKING_NEWS_EXPORT_URL") or build_export_url(DOC_ID)

BREAKING_NEWS_CSS = """
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
.bn-headline {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 1.5em;
    font-weight: 600;
    margin: 4px 0 2px;
}
.bn-timestamp {
    font-family: 'Oxanium', monospace;
    font-size: 0.72em;
    color: #999;
    font-weight: 700;
}
.bn-interpretation {
    font-family: 'Crimson Text', Georgia, serif;
    font-size: 1.08em;
    line-height: 1.55;
    margin: 10px 0 14px;
}
.bn-facts-box {
    max-height: 260px;
    overflow-y: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 4px 2px;
    border-top: 1px solid #eee;
}
.bn-fact {
    display: inline-block;
    border-radius: 14px;
    padding: 4px 12px;
    font-size: 0.78em;
    line-height: 1.4;
    max-width: 100%;
}
.bn-fact-physical { background: #f4ecf9; color: #5b2d7a; border: 1px solid #d9c2eb; }
.bn-fact-market    { background: #eafaf1; color: #1b7a44; border: 1px solid #b7ecd0; }
</style>
"""


def _inject_breaking_news_js() -> None:
    """ESC → back button. Same guard pattern as terminal/chat JS."""
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            function fireBack(e) {
                if (e.key !== 'Escape') return;
                var btn = doc.querySelector('.st-key-bn_back button');
                if (btn) btn.click();
            }
            if (doc.__bn_esc__) doc.removeEventListener('keydown', doc.__bn_esc__);
            doc.__bn_esc__ = fireBack;
            doc.addEventListener('keydown', fireBack);

            function attachToIframes() {
                doc.querySelectorAll('iframe').forEach(function(f) {
                    try {
                        if (!f.__bn_attached__) {
                            f.__bn_attached__ = true;
                            f.contentDocument.addEventListener('keydown', fireBack);
                        }
                    } catch (ignore) {}
                });
            }
            attachToIframes();
            if (doc.__bn_obs__) { try { doc.__bn_obs__.disconnect(); } catch(_) {} }
            doc.__bn_obs__ = new MutationObserver(attachToIframes);
            doc.__bn_obs__.observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=1,
    )


def _do_fetch_and_reparse() -> None:
    """Fetch + parse the doc, updating session cache. Never raises.

    A source-format error must never clobber an already-good cache — only a
    fully clean parse (reports present, zero errors) replaces
    bn_cache_reports. If errors are present but no cache exists yet, the
    partial parse is still shown (better than a blank view) alongside a
    visible warning.
    """
    try:
        text = fetch_doc_text(EXPORT_URL)
        reports, errors = parse_reports(text)
    except BreakingNewsAccessError as e:
        st.session_state.bn_stale = True
        st.session_state.bn_last_status = f"access error: {e}"
        st.session_state.bn_last_fetch = time.monotonic()
        return

    if not reports and not errors:
        st.session_state.bn_stale = True
        st.session_state.bn_last_status = "no valid reports found in source document"
        st.session_state.bn_last_fetch = time.monotonic()
        return

    has_existing_cache = bool(st.session_state.get("bn_cache_reports"))

    if errors and has_existing_cache:
        # Preserve the last good dataset — do not let a partial reparse
        # (e.g. a malformed newly-appended report) mutate the working cache.
        st.session_state.bn_cache_errors = errors
        st.session_state.bn_stale = True
        st.session_state.bn_last_status = f"{len(errors)} report(s) failed to parse; showing last good data"
        st.session_state.bn_last_fetch = time.monotonic()
        return

    if not reports:
        # errors only, and no prior cache to fall back on
        st.session_state.bn_cache_errors = errors
        st.session_state.bn_stale = True
        st.session_state.bn_last_status = "source document has no valid reports (format errors only)"
        st.session_state.bn_last_fetch = time.monotonic()
        return

    # Fully clean parse, or a first-ever partial parse with no prior cache.
    st.session_state.bn_cache_reports = reports
    st.session_state.bn_cache_errors = errors
    st.session_state.bn_stale = bool(errors)
    st.session_state.bn_last_status = (
        f"{len(errors)} report(s) skipped due to source-format errors" if errors else ""
    )
    st.session_state.bn_last_fetch = time.monotonic()


def _render_fact_bubbles(facts: list[str], css_class: str) -> str:
    return "".join(
        f'<span class="bn-fact {css_class}">{html.escape(fact)}</span>' for fact in facts
    )


def compute_selection(
    ids: list[str], prev_newest: str | None, current_sel: str | None
) -> tuple[str | None, str | None]:
    """Decide the next (selected_id, prev_newest_id) given the current
    newest-first id list and prior state. No Streamlit dependency, so this
    is directly unit-testable.

    Advances to the newest id only if the viewer was already on what was
    previously the newest (or had no selection yet); otherwise preserves a
    deliberately-chosen older selection. Falls back to newest if the current
    selection no longer exists in ids at all.
    """
    if not ids:
        return current_sel, prev_newest

    newest_id = ids[0]
    selected = current_sel

    if newest_id != prev_newest:
        if selected is None or selected == prev_newest:
            selected = newest_id
    if selected not in ids:
        selected = newest_id

    return selected, newest_id


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def _breaking_news_body() -> None:
    now = time.monotonic()
    last = st.session_state.get("bn_last_fetch")
    if last is None or now - last >= POLL_INTERVAL_SECONDS:
        _do_fetch_and_reparse()

    reports = st.session_state.get("bn_cache_reports", [])
    errors = st.session_state.get("bn_cache_errors", [])

    if not reports:
        status = st.session_state.get("bn_last_status")
        if status:
            st.error(f"No Breaking News reports available: {status}")
        else:
            st.info("No Breaking News reports available yet.")
        return

    # ── auto-advance / dropdown selection ──────────────────────────────────
    ids = sorted((r.report_id for r in reports), reverse=True)
    new_sel, new_prev_newest = compute_selection(
        ids,
        st.session_state.get("bn_prev_newest_id"),
        st.session_state.get("bn_dropdown"),
    )
    st.session_state["bn_dropdown"] = new_sel
    st.session_state["bn_prev_newest_id"] = new_prev_newest

    id_to_report = {r.report_id: r for r in reports}

    st.selectbox(
        "Report",
        options=ids,
        key="bn_dropdown",
        format_func=lambda rid: f"{id_to_report[rid].headline} — {id_to_report[rid].timestamp_display}",
        label_visibility="collapsed",
    )

    if st.session_state.get("bn_stale"):
        st.caption(f"⚠ showing cached data — {st.session_state.get('bn_last_status', 'last refresh failed')}")

    if errors:
        names = ", ".join(f"{e.report_id or '?'} ({e.detail})" for e in errors)
        st.caption(f"⚠ {len(errors)} report(s) skipped due to source-format errors: {names}")

    report = id_to_report[st.session_state["bn_dropdown"]]

    empty_core = []
    if not report.physical_facts:
        empty_core.append("Physical-balance facts")
    if not report.market_facts:
        empty_core.append("Market-pricing facts")
    if not report.interpretation.strip():
        empty_core.append("Interpretation")
    if empty_core:
        st.warning(f"⚠ This report is missing content for: {', '.join(empty_core)}. Showing available content only.")

    st.markdown(f'<div class="bn-headline">{html.escape(report.headline)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="bn-timestamp">{html.escape(report.timestamp_display)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="bn-interpretation">{html.escape(report.interpretation)}</div>', unsafe_allow_html=True)

    bubbles = _render_fact_bubbles(report.physical_facts, "bn-fact-physical")
    bubbles += _render_fact_bubbles(report.market_facts, "bn-fact-market")
    st.markdown(f'<div class="bn-facts-box">{bubbles}</div>', unsafe_allow_html=True)


def render_breaking_news() -> None:
    st.markdown(BREAKING_NEWS_CSS, unsafe_allow_html=True)
    _inject_breaking_news_js()

    col_back, col_title = st.columns([1, 9])
    with col_back:
        if st.button("← Back", key="bn_back"):
            st.session_state.mode = "newscenter"
            st.rerun()
    with col_title:
        st.markdown(
            "<p style='font-family:\"Oxanium\",monospace;font-weight:700;font-size:1.1em;"
            "color:#999;padding-top:6px;margin:0;letter-spacing:0.06em'>BREAKING NEWS</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='margin:6px 0 10px'>", unsafe_allow_html=True)

    _breaking_news_body()
