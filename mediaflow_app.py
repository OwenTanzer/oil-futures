"""
MediaFlow dashboard — Iran/Hormuz crisis monitor.
Run with: streamlit run mediaflow_app.py
"""

import base64
import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import streamlit as st
from breaking_news_view import render_breaking_news
from eia_terminal import render_terminal
from mediaflow_chat import render_chat
from worker_state import load_cycle_state, request_refresh

HERE            = Path(__file__).parent
DATA_DIR        = Path(os.environ.get("DATA_DIR", HERE))
CLASSIFIED_FILE = DATA_DIR / "mediaflow_classified.json"
ITEMS_FILE      = DATA_DIR / "mediaflow_items.json"

ARC_COLOR = {
    "KINETIC":        "#c0392b",
    "DIPLOMATIC":     "#2980b9",
    "STRAIT_SHIPPING":"#d35400",
    "MARKET":         "#27ae60",
    "IEA_SUPPLY":     "#8e44ad",
}

ARC_LABEL = {
    "KINETIC":        "Kinetic",
    "DIPLOMATIC":     "Diplomatic",
    "STRAIT_SHIPPING":"Maritime",
    "MARKET":         "Financial",
    "IEA_SUPPLY":     "Physical Supply",
}


# ── date helpers ──────────────────────────────────────────────────────────────

def parse_dt(s: str) -> datetime:
    if not s or s == "unknown":
        return datetime.min.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    return datetime.min.replace(tzinfo=timezone.utc)


def fmt_dt_utc(s: str) -> tuple[str, str]:
    dt = parse_dt(s)
    if dt == datetime.min.replace(tzinfo=timezone.utc):
        return "—", ""
    return dt.strftime("%b %d  %H:%M UTC"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")


DISPLAY_RELOAD_INTERVAL_MS = 2 * 60 * 1000  # 2 minutes


def inject_hotkey_listener() -> None:
    """Bind T key → click the terminal button.

    Called every time the newscenter renders. Uses stored references on the
    parent document to replace (not accumulate) the listener and MutationObserver
    each run. Boolean guards caused the bug: when entering terminal mode the
    hotkey iframe is destroyed, killing its MutationObserver; on return the
    guards prevented re-setup, so new Streamlit iframes never got the handler.
    """
    st.iframe(
        """
        <script>
        (function() {
            var doc = window.parent.document;

            function fireTerminal(e) {
                if (e.key !== 't' && e.key !== 'T') return;
                if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
                if (e.ctrlKey || e.metaKey || e.altKey) return;
                var btn = doc.querySelector('.st-key-goto_terminal button');
                if (btn) btn.click();
            }

            // Replace parent listener — always use the freshest closure.
            if (doc.__hotkey_fn__) doc.removeEventListener('keydown', doc.__hotkey_fn__);
            doc.__hotkey_fn__ = fireTerminal;
            doc.addEventListener('keydown', fireTerminal);

            // Attach to all current Streamlit iframes.
            function attachToIframes() {
                doc.querySelectorAll('iframe').forEach(function(f) {
                    try {
                        if (!f.__hotkey_t__) {
                            f.__hotkey_t__ = true;
                            f.contentDocument.addEventListener('keydown', fireTerminal);
                        }
                    } catch (ignore) {}
                });
            }
            attachToIframes();

            // Replace MutationObserver — the old one dies with its iframe.
            if (doc.__hotkey_obs__) { try { doc.__hotkey_obs__.disconnect(); } catch(_) {} }
            doc.__hotkey_obs__ = new MutationObserver(attachToIframes);
            doc.__hotkey_obs__.observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=1,
    )


def inject_tz_converter() -> None:
    """Renders once per full page load. Handles timezone conversion and
    periodic page reload so backgrounded tabs stay current."""
    st.iframe(
        f"""
        <script>
        function convertTimestamps() {{
            try {{
                var els = window.parent.document.querySelectorAll('[data-utc]');
                els.forEach(function(el) {{
                    var utc = el.getAttribute('data-utc');
                    if (!utc || el.getAttribute('data-converted')) return;
                    var dt = new Date(utc);
                    if (isNaN(dt)) return;
                    el.textContent = dt.toLocaleString('en-US', {{
                        month: 'short', day: 'numeric',
                        hour: '2-digit', minute: '2-digit',
                        timeZoneName: 'short'
                    }});
                    el.setAttribute('data-converted', '1');
                }});
            }} catch(e) {{}}
        }}
        convertTimestamps();
        var observer = new MutationObserver(convertTimestamps);
        observer.observe(window.parent.document.body, {{childList: true, subtree: true}});

        // Reload every 2 minutes regardless of tab focus state.
        // Page reloads pick up fresh data from the background collector.
        setInterval(function() {{
            window.parent.location.reload();
        }}, {DISPLAY_RELOAD_INTERVAL_MS});
        </script>
        """,
        height=1,
    )


# ── data ──────────────────────────────────────────────────────────────────────

def load_classified() -> list[dict]:
    if not CLASSIFIED_FILE.exists():
        return []
    data = json.loads(CLASSIFIED_FILE.read_text(encoding="utf-8"))
    return sorted(data, key=lambda x: parse_dt(x.get("published", "")), reverse=True)


def item_counts() -> tuple[int, int]:
    n_items = 0
    n_classified = 0
    if ITEMS_FILE.exists():
        n_items = len(json.loads(ITEMS_FILE.read_text(encoding="utf-8")))
    if CLASSIFIED_FILE.exists():
        n_classified = len(json.loads(CLASSIFIED_FILE.read_text(encoding="utf-8")))
    return n_items, n_classified


# ── rendering ─────────────────────────────────────────────────────────────────

def render_item(item: dict, show_arc_tag: bool = False) -> None:
    arc      = item.get("arc", "")
    color    = ARC_COLOR.get(arc, "#999")
    conflict = item.get("conflict", False)
    ts_display, ts_iso = fmt_dt_utc(item.get("published", ""))
    source   = item.get("source", "")
    summary  = item.get("arc_summary") or item.get("title", "")
    link     = item.get("link", "#")

    arc_tag = ""
    if show_arc_tag and arc:
        label = ARC_LABEL.get(arc, arc)
        arc_tag = f'<span class="arc-label" style="font-size:0.83em;color:{color};font-weight:800;text-transform:uppercase;letter-spacing:0.04em">{label}&ensp;</span>'

    conflict_mark = (
        '<span style="color:#c0392b;font-weight:700" title="Conflicting claims reported">⚡</span> '
        if conflict else ""
    )

    ts_attr = f'data-utc="{ts_iso}"' if ts_iso else ""

    st.markdown(
        f"""<div style="border-left:3px solid {color};padding:7px 12px;margin-bottom:10px;">
{arc_tag}<span class="meta-text" style="color:#999;font-size:0.72em"><span {ts_attr}>{ts_display}</span> &nbsp;·&nbsp; {source}</span><br>
{conflict_mark}<span class="main-text">{summary}</span><br>
<a href="{link}" target="_blank" class="meta-text" style="font-size:0.72em;color:#999;text-decoration:none">→ source</a>
</div>""",
        unsafe_allow_html=True,
    )


# ── status/update fragment ────────────────────────────────────────────────────

WORKER_INTERVAL_SECONDS = int(os.environ.get("COLLECT_INTERVAL_SECONDS", 900))
STALE_THRESHOLD_SECONDS = WORKER_INTERVAL_SECONDS * 2


@st.fragment(run_every=10)
def render_status_and_update() -> None:
    """Read-only freshness/health display, plus the refresh-request button.
    The dashboard never runs collect/classify itself — worker.py is the sole
    writer; this only enqueues a request and polls the worker's cycle-state."""

    state = load_cycle_state()
    now = datetime.now(timezone.utc)

    updated_display = "—"
    ts_attr = ""
    health_html = ""

    if state and state.get("last_success"):
        last_success = datetime.strptime(state["last_success"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        updated_display = last_success.strftime("%H:%M UTC")
        ts_attr = f'data-utc="{state["last_success"]}"'

        age = (now - last_success).total_seconds()
        if age > STALE_THRESHOLD_SECONDS:
            health_html = '<div style="text-align:center;font-size:0.55em;color:#c0392b;font-family:\'Oxanium\',monospace;font-weight:700">stale — no recent cycle</div>'

    if state and state.get("last_error"):
        health_html = '<div style="text-align:center;font-size:0.55em;color:#c0392b;font-family:\'Oxanium\',monospace;font-weight:700">last cycle failed</div>'

    st.markdown(
        f"<div style='text-align:center;font-size:0.58em;color:#999;font-family:\"Oxanium\",monospace;font-weight:700;white-space:nowrap;padding:1px 0 3px'>updated <span {ts_attr}>{updated_display}</span></div>"
        + health_html,
        unsafe_allow_html=True,
    )
    if st.button("Update", type="secondary", use_container_width=True):
        request_refresh()
        st.toast("Refresh requested — worker will pick it up shortly.")


# ── live feed fragment ────────────────────────────────────────────────────────

ITEMS_PER_ARC = 40

@st.fragment(run_every=30)
def live_feed() -> None:
    """Display-only fragment. Polls for new data every 30s."""

    items = load_classified()

    if not items:
        st.info("No classified items yet. Click 'Update feed' to seed the feed.")
        return

    arc_keys = list(ARC_LABEL.keys())
    other_items = [i for i in items if i.get("arc") not in ARC_LABEL]
    all_limit = ITEMS_PER_ARC * len(arc_keys)
    total_items = len(items)
    visible_all = min(total_items, all_limit)

    main_items = [i for i in items if i.get("arc") in ARC_LABEL]

    tab_labels = ["All"] + list(ARC_LABEL.values())
    if other_items:
        tab_labels.append("Other")
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        for item in main_items[:all_limit]:
            render_item(item, show_arc_tag=True)

    for tab, arc in zip(tabs[1:], arc_keys):
        with tab:
            arc_items = [i for i in items if i.get("arc") == arc]
            if not arc_items:
                st.caption("No items.")
            for item in arc_items[:ITEMS_PER_ARC]:
                render_item(item, show_arc_tag=False)

    if other_items:
        with tabs[-1]:
            for item in other_items[:ITEMS_PER_ARC]:
                render_item(item, show_arc_tag=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="MediaFlow: the Iran-Hormuz Crisis",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if st.session_state.get("mode") == "terminal":
        render_terminal()
        return

    if st.session_state.get("mode") == "chat":
        render_chat()
        return

    if st.session_state.get("mode") == "breaking_news":
        render_breaking_news()
        return

    # Rendered once per full page load — MutationObserver stays alive
    # for the entire session, converting timestamps as the fragment adds them.
    inject_tz_converter()

    st.markdown(
        """<style>
        @import url('https://fonts.googleapis.com/css2?family=Crimson+Text:ital,wght@0,400;0,600;1,400&family=Oxanium:wght@700&display=swap');
        :root { color-scheme: light; }
        [data-testid="stAppViewContainer"] { background: #fff; }
        [data-testid="stSidebar"] { display: none; }
        [data-testid="collapsedControl"] { display: none; }
        [data-testid="stHeader"] { display: none; }
        [data-testid="stToolbar"] { display: none; }
        .block-container { padding-top: 0.4rem !important; padding-bottom: 0 !important; }
        .stTabs [data-baseweb="tab-list"] { gap: 4px; margin-top: 0 !important; }
        .stTabs [data-baseweb="tab"] { padding: 6px 14px; }
        .stTabs [data-baseweb="tab"] *,
        .stTabs [data-baseweb="tab"] { font-family: 'Crimson Text', Georgia, serif !important; font-size: 0.98em !important; }
        hr { margin: 0.3rem 0 !important; }
        .stCaption { margin-bottom: 0 !important; }
        h1, h2, h3 { margin-top: 0 !important; margin-bottom: 0 !important; }
        body, .stMarkdown, .stCaption, button { font-family: 'Crimson Text', Georgia, serif !important; }
        .main-text { font-family: 'Crimson Text', Georgia, serif; font-size: 1.05em; line-height: 1.5; }
        .arc-label { font-family: 'Crimson Text', Georgia, serif; }
        .meta-text { font-family: 'Oxanium', monospace; font-weight: 700; }
        div[data-testid="stButton"] > button,
        div[data-testid="stButton"] > button > div,
        div[data-testid="stButton"] > button p {
            font-family: 'Oxanium', monospace !important;
            font-weight: 700 !important;
            font-size: 1.35em !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    # ── header ────────────────────────────────────────────────────────────────
    col1, col2 = st.columns([7.8, 1.775])
    with col1:
        img_b64 = base64.b64encode((HERE / "55cb4ced-c8a8-4188-9ff7-376c5a52935b.png").read_bytes()).decode()
        st.markdown(
            f'<img src="data:image/png;base64,{img_b64}" style="width:100%;display:block;">',
            unsafe_allow_html=True,
        )
    with col2:
        render_status_and_update()
        ba, bb, bc, bd = st.columns(4)
        with ba:
            if st.button("⊹", key="goto_terminal", help="Terminal  [T]", use_container_width=True):
                st.session_state.mode = "terminal"
                st.rerun()
        with bb:
            if st.button("✦", key="goto_chat", help="Agent", use_container_width=True):
                st.session_state.mode = "chat"
                st.rerun()
        with bc:
            if st.button("⚡", key="goto_breaking_news", help="Breaking News", use_container_width=True):
                st.session_state.mode = "breaking_news"
                st.session_state.pop("bn_last_fetch", None)  # force fresh fetch on entry
                st.rerun()
        with bd:
            st.button("□", key="dash_d", use_container_width=True)

    # ── live feed ─────────────────────────────────────────────────────────────
    inject_hotkey_listener()
    live_feed()


if __name__ == "__main__":
    main()
