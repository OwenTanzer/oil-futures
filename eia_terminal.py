"""
EIA Petroleum Terminal — command-line style interface.
Commands: stocks [year|y1-y2]  crude [year|y1-y2]  ls  help  clear
Invoked by mediaflow_app.py when session_state.mode == "terminal".
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from futures_market import MarketDataError, default_market_service

DATA_DIR = Path(__file__).parent / "data"

SERIES_META: dict[str, dict] = {
    "commercial_crude_exSPR": {
        "label":   "Commercial Crude Stocks (ex-SPR)",
        "unit":    "Million Barrels",
        "color":   "#27ae60",
        "aliases": ["stocks", "crude", "inventory"],
    },
    "refinery_input": {
        "label":   "Refinery Input (Crude Runs)",
        "unit":    "Thousand Barrels/Day",
        "color":   "#2980b9",
        "aliases": ["demand", "refinery", "runs"],
    },
    "crude_exports": {
        "label":   "Crude Oil Exports",
        "unit":    "Thousand Barrels/Day",
        "color":   "#e67e22",
        "aliases": ["exports"],
    },
    "crude_imports": {
        "label":   "Crude Oil Imports",
        "unit":    "Thousand Barrels/Day",
        "color":   "#8e44ad",
        "aliases": ["imports"],
    },
    "crude_production": {
        "label":   "Domestic Crude Production",
        "unit":    "Thousand Barrels/Day",
        "color":   "#c0392b",
        "aliases": ["production", "prod"],
    },
}

# Map alias → series key
_ALIAS_MAP: dict[str, str] = {}
for _k, _v in SERIES_META.items():
    for _a in _v.get("aliases", []):
        _ALIAS_MAP[_a] = _k
    _ALIAS_MAP[_k] = _k

TERMINAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Oxanium:wght@400;700&display=swap');
:root { color-scheme: light; }
[data-testid="stAppViewContainer"] { background: #fff !important; }
[data-testid="stSidebar"]          { display: none; }
[data-testid="stHeader"]           { display: none; }
[data-testid="stToolbar"]          { display: none; }
[data-testid="collapsedControl"]   { display: none; }
.block-container { padding-top: 1rem !important; padding-bottom: 4rem !important; }
* { font-family: 'Oxanium', monospace !important; }
h1, h2, h3, h4, label, p, div, span, button { color: #1a1a1a !important; }
hr { border-color: #ddd !important; }
.term-line { font-size: 0.88em; margin: 0; padding: 0; line-height: 1.6; }
.term-cmd  { color: #1a1a1a !important; font-weight: 700; }
.term-err  { color: #c0392b !important; }
.term-out  { color: #444; }
.term-dim  { color: #999; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.cursor { animation: blink 1s step-end infinite; color: #1a1a1a; font-weight: 700; }
[data-testid="stChatInput"] textarea {
    background: #fff !important;
    color: #1a1a1a !important;
    border: 1px solid #ccc !important;
    font-family: 'Oxanium', monospace !important;
    caret-color: #1a1a1a;
}
[data-testid="stChatInput"] textarea:focus {
    outline: none !important;
    box-shadow: 0 0 0 2px #bbb !important;
    border-color: #bbb !important;
}
</style>
"""

HELP_TEXT = """\
COMMANDS
  stocks [year|y1-y2]         commercial crude stocks (ex-SPR)   [MB]
  demand [year|y1-y2]         refinery crude input                [kb/d]
  exports [year|y1-y2]        crude oil exports                   [kb/d]
  imports [year|y1-y2]        crude oil imports                   [kb/d]
  production [year|y1-y2]     domestic crude production           [kb/d]
  aggregate_demand [year|y1-y2]
                              refinery input + exports             [kb/d]
  aggregate_supply [year|y1-y2]
                              imports + production                 [kb/d]
  curve [6|12|18]             WTI futures curve + calendar spreads [$/bbl]
  cracks [6|12]               gasoline, distillate, 3-2-1 cracks   [$/bbl]
  ls                          list available data series
  clear                       clear terminal history
  back / exit                 return to news feed
  help                        show this message

ALIASES  crude/inventory=stocks  refinery/runs=demand  prod=production
EXAMPLES  stocks 2026   curve 12   cracks 6   aggregate_supply
"""


@st.cache_data(ttl=300)
def _load_series() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    if not DATA_DIR.exists():
        return result
    for csv_path in sorted(DATA_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path, parse_dates=["data_date"])
            df = df.sort_values("data_date").reset_index(drop=True)
            result[csv_path.stem] = df
        except Exception:
            pass
    return result


def _label(key: str) -> str:
    return SERIES_META.get(key, {}).get("label", key.replace("_", " ").title())


def _parse_year_range(arg: str) -> tuple[date | None, date | None]:
    """Parse '2026' or '2020-2026' into (start_date, end_date). Returns (None, None) on failure."""
    m = re.fullmatch(r"(\d{4})-(\d{4})", arg)
    if m:
        return date(int(m.group(1)), 1, 1), date(int(m.group(2)), 12, 31)
    m = re.fullmatch(r"(\d{4})", arg)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    return None, None


def _build_chart(series_keys: list[str], start: date | None, end: date | None, show_sum: bool = False) -> go.Figure:
    series_data = _load_series()
    fig = go.Figure()
    for key in series_keys:
        df = series_data.get(key)
        if df is None:
            continue
        mask = pd.Series([True] * len(df))
        if start:
            mask &= df["data_date"].dt.date >= start
        if end:
            mask &= df["data_date"].dt.date <= end
        df_f = df[mask]
        val_col = next((c for c in df.columns if c != "data_date"), None)
        if val_col is None:
            continue
        meta = SERIES_META.get(key, {})
        fig.add_trace(go.Scatter(
            x=df_f["data_date"],
            y=df_f[val_col],
            mode="lines+markers",
            name=_label(key),
            line=dict(color=meta.get("color", "#00aaff"), width=2),
            marker=dict(size=4),
            hovertemplate="%{x|%b %d %Y}<br>%{y:,.1f} MB<extra></extra>",
        ))
    # Optional sum trace (for aggregate_demand)
    if show_sum and len(series_keys) >= 2:
        series_data = _load_series()
        frames: list[pd.DataFrame] = []
        for key in series_keys:
            df = series_data.get(key)
            if df is None:
                continue
            mask = pd.Series([True] * len(df))
            if start:
                mask &= df["data_date"].dt.date >= start
            if end:
                mask &= df["data_date"].dt.date <= end
            val_col = next((c for c in df.columns if c != "data_date"), None)
            if val_col:
                frames.append(df[mask][["data_date", val_col]].rename(columns={val_col: key}))
        if frames:
            merged = frames[0]
            for f in frames[1:]:
                merged = merged.merge(f, on="data_date", how="inner")
            merged["_sum"] = merged[series_keys].sum(axis=1)
            fig.add_trace(go.Scatter(
                x=merged["data_date"],
                y=merged["_sum"],
                mode="lines",
                name="Aggregate Demand",
                line=dict(color="#1a1a1a", width=2.5, dash="dot"),
                hovertemplate="%{x|%b %d %Y}<br>%{y:,.0f} kb/d<extra>Aggregate</extra>",
            ))

    unit = SERIES_META.get(series_keys[0], {}).get("unit", "MB") if len(series_keys) == 1 else "Thousand Barrels/Day"
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#fff",
        plot_bgcolor="#fafafa",
        font=dict(family="Oxanium, monospace", size=12, color="#444"),
        margin=dict(l=50, r=20, t=20, b=50),
        yaxis=dict(title=unit, gridcolor="#e8e8e8", zerolinecolor="#ccc"),
        xaxis=dict(gridcolor="#e8e8e8", zerolinecolor="#ccc"),
        legend=dict(orientation="h", y=-0.18, font=dict(size=11)),
        height=380,
        hovermode="x unified",
    )
    return fig


def _build_curve_chart(snapshot: dict[str, Any]) -> go.Figure:
    quotes = snapshot["quotes"]
    spreads = snapshot["calendar_spreads"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[row["delivery_month"] for row in quotes],
        y=[row["price"] for row in quotes],
        customdata=[[
            row["symbol"],
            row["quote_time"],
            row.get("last_trade_date") or "unknown",
            row.get("volume"),
        ] for row in quotes],
        mode="lines+markers",
        name="WTI futures",
        line=dict(color="#1a1a1a", width=2.5),
        marker=dict(size=7),
        hovertemplate=(
            "%{x}<br>%{customdata[0]}<br>$%{y:.2f}/bbl"
            "<br>Quote: %{customdata[1]}<br>Last trade: %{customdata[2]}"
            "<br>Volume: %{customdata[3]}<extra>WTI</extra>"
        ),
    ))
    fig.add_trace(go.Bar(
        x=[row["front_month"] for row in spreads],
        y=[row["spread"] for row in spreads],
        name="Front - next",
        marker_color=["#c0392b" if row["spread"] < 0 else "#27ae60" for row in spreads],
        opacity=0.45,
        yaxis="y2",
        hovertemplate="%{x}<br>$%{y:.2f}/bbl<extra>Calendar spread</extra>",
    ))
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#fff",
        plot_bgcolor="#fafafa",
        font=dict(family="Oxanium, monospace", size=12, color="#444"),
        margin=dict(l=50, r=50, t=20, b=60),
        yaxis=dict(title="WTI price ($/bbl)", gridcolor="#e8e8e8"),
        yaxis2=dict(title="Front - next ($/bbl)", overlaying="y", side="right", zeroline=True),
        xaxis=dict(title="Delivery month", gridcolor="#e8e8e8"),
        legend=dict(orientation="h", y=-0.22),
        height=400,
        hovermode="x unified",
        barmode="overlay",
    )
    return fig


def _build_cracks_chart(snapshot: dict[str, Any]) -> go.Figure:
    rows = snapshot["rows"]
    colors = {"gasoline": "#e67e22", "distillate": "#2980b9", "three_two_one": "#1a1a1a"}
    labels = {"gasoline": "Gasoline", "distillate": "Distillate", "three_two_one": "3-2-1"}
    fig = go.Figure()
    for key in ("gasoline", "distillate", "three_two_one"):
        fig.add_trace(go.Scatter(
            x=[row["delivery_month"] for row in rows],
            y=[row[key] for row in rows],
            customdata=[[
                row["cl"]["symbol"], row["cl"]["price"], row["cl"]["quote_time"],
                row["rb"]["symbol"], row["rb"]["price"], row["rb"]["quote_time"],
                row["ho"]["symbol"], row["ho"]["price"], row["ho"]["quote_time"],
            ] for row in rows],
            mode="lines+markers",
            name=labels[key],
            line=dict(color=colors[key], width=2.5 if key == "three_two_one" else 2),
            marker=dict(size=6),
            hovertemplate=(
                "%{x}<br>Spread: $%{y:.2f}/bbl"
                "<br>CL %{customdata[0]}: $%{customdata[1]:.3f}/bbl at %{customdata[2]}"
                "<br>RB %{customdata[3]}: $%{customdata[4]:.4f}/gal at %{customdata[5]}"
                "<br>HO %{customdata[6]}: $%{customdata[7]:.4f}/gal at %{customdata[8]}"
                "<extra>" + labels[key] + "</extra>"
            ),
        ))
    fig.add_hline(y=0, line_color="#999", line_width=1)
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#fff",
        plot_bgcolor="#fafafa",
        font=dict(family="Oxanium, monospace", size=12, color="#444"),
        margin=dict(l=50, r=20, t=20, b=60),
        yaxis=dict(title="Implied margin ($/bbl)", gridcolor="#e8e8e8"),
        xaxis=dict(title="Matched delivery month", gridcolor="#e8e8e8"),
        legend=dict(orientation="h", y=-0.22),
        height=400,
        hovermode="x unified",
    )
    return fig


def _parse_market_months(args: list[str], allowed: tuple[int, ...], default: int, command: str) -> int:
    if not args:
        return default
    if len(args) != 1 or not args[0].isdigit() or int(args[0]) not in allowed:
        values = "|".join(str(value) for value in allowed)
        raise MarketDataError(f"Usage: {command} [{values}]")
    return int(args[0])


def _execute(cmd_str: str) -> list[dict[str, Any]]:
    """Parse and execute a command. Returns list of output items."""
    parts = cmd_str.strip().split()
    if not parts:
        return []
    verb = parts[0].lower()
    args = parts[1:]

    # navigation
    if verb in ("back", "exit", "quit"):
        st.session_state.mode = "newscenter"
        st.rerun()

    # clear
    if verb == "clear":
        st.session_state.term_history = []
        return []

    # help
    if verb == "help":
        return [{"type": "text", "text": HELP_TEXT}]

    # ls / list
    if verb in ("ls", "list", "series"):
        series_data = _load_series()
        if not series_data:
            return [{"type": "error", "text": "No data found in data/. Run build_timeseries.py."}]
        lines = ["Available series:"]
        for k in series_data:
            meta = SERIES_META.get(k, {})
            aliases = ", ".join(meta.get("aliases", []))
            lines.append(f"  {k}  ({aliases})" if aliases else f"  {k}")
        lines.extend([
            "  WTI explicit-month futures curve  (curve)",
            "  CL calendar spreads              (included with curve)",
            "  refinery crack spreads           (cracks)",
        ])
        return [{"type": "text", "text": "\n".join(lines)}]

    if verb == "curve":
        try:
            months = _parse_market_months(args, (6, 12, 18), 12, "curve")
            snapshot = default_market_service().curve(months)
        except MarketDataError as exc:
            return [{"type": "error", "text": str(exc)}]
        return [{"type": "market_curve", "snapshot": snapshot}]

    if verb in ("cracks", "crack"):
        try:
            months = _parse_market_months(args, (6, 12), 6, "cracks")
            snapshot = default_market_service().cracks(months)
        except MarketDataError as exc:
            return [{"type": "error", "text": str(exc)}]
        return [{"type": "market_cracks", "snapshot": snapshot}]

    # data commands (stocks / crude / series key)
    series_key = _ALIAS_MAP.get(verb)
    if series_key:
        series_data = _load_series()
        if series_key not in series_data:
            return [{"type": "error", "text": f"Series '{series_key}' not found in data/. Run build_timeseries.py."}]

        start: date | None = None
        end: date | None = None
        if args:
            start, end = _parse_year_range(args[0])
            if start is None:
                return [{"type": "error", "text": f"Unrecognised date format '{args[0]}'. Use YYYY or YYYY-YYYY."}]

        df = series_data[series_key]
        val_col = next((c for c in df.columns if c != "data_date"), None)
        mask = pd.Series([True] * len(df))
        if start:
            mask &= df["data_date"].dt.date >= start
        if end:
            mask &= df["data_date"].dt.date <= end
        n = mask.sum()
        if n == 0:
            return [{"type": "error", "text": "No data in that range."}]

        label_str = _label(series_key)
        range_str = f"{start} → {end}" if start else "all time"
        return [
            {"type": "info", "text": f"{label_str}  ·  {range_str}  ·  {n} points"},
            {"type": "chart", "series": [series_key], "start": start, "end": end},
        ]

    # aggregate_supply = crude_imports + crude_production
    if verb in ("aggregate_supply", "agg_supply", "total_supply"):
        series_data = _load_series()
        agg_keys = ["crude_imports", "crude_production"]
        missing = [k for k in agg_keys if k not in series_data]
        if missing:
            return [{"type": "error", "text": f"Missing series: {missing}. Run build_timeseries.py."}]

        start: date | None = None
        end: date | None = None
        if args:
            start, end = _parse_year_range(args[0])
            if start is None:
                return [{"type": "error", "text": f"Unrecognised date format '{args[0]}'. Use YYYY or YYYY-YYYY."}]

        range_str = f"{start} to {end}" if start else "all time"
        return [
            {"type": "info", "text": f"Aggregate Supply (Imports + Production)  ·  {range_str}  ·  kb/d"},
            {"type": "chart", "series": agg_keys, "start": start, "end": end, "show_sum": True},
        ]

    # aggregate_demand = refinery_input + crude_exports
    if verb in ("aggregate_demand", "agg", "total_demand"):
        series_data = _load_series()
        agg_keys = ["refinery_input", "crude_exports"]
        missing = [k for k in agg_keys if k not in series_data]
        if missing:
            return [{"type": "error", "text": f"Missing series: {missing}. Run build_timeseries.py."}]

        start: date | None = None
        end: date | None = None
        if args:
            start, end = _parse_year_range(args[0])
            if start is None:
                return [{"type": "error", "text": f"Unrecognised date format '{args[0]}'. Use YYYY or YYYY-YYYY."}]

        range_str = f"{start} to {end}" if start else "all time"
        return [
            {"type": "info", "text": f"Aggregate Demand (Refinery Input + Crude Exports)  ·  {range_str}  ·  kb/d"},
            {"type": "chart", "series": agg_keys, "start": start, "end": end, "show_sum": True},
        ]

    return [{"type": "error", "text": f"Unknown command: '{verb}'. Type 'help' for commands."}]


def _inject_terminal_js() -> None:
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;

            // ── ESC → back ────────────────────────────────────────────────────
            function fireBack(e) {
                if (e.key !== 'Escape') return;
                var btn = doc.querySelector('.st-key-terminal_back button');
                if (btn) btn.click();
            }
            if (doc.__esc_fn__) doc.removeEventListener('keydown', doc.__esc_fn__);
            doc.__esc_fn__ = fireBack;
            doc.addEventListener('keydown', fireBack);

            // ── auto-focus chat input ─────────────────────────────────────────
            function focusChat() {
                var ta = doc.querySelector('[data-testid="stChatInput"] textarea');
                if (ta) ta.focus();
            }

            // Focus on load and after every rerun (this script re-runs each time).
            setTimeout(focusChat, 150);

            // Re-focus after any click anywhere in the page.
            function onDocClick(e) {
                // Don't steal focus mid-button-press.
                if (e.target.closest('button, a, select, [role="option"]')) return;
                setTimeout(focusChat, 80);
            }
            if (doc.__focus_click__) doc.removeEventListener('click', doc.__focus_click__);
            doc.__focus_click__ = onDocClick;
            doc.addEventListener('click', onDocClick);

            // Attach ESC to child iframes (Plotly etc.).
            function attachToIframes() {
                doc.querySelectorAll('iframe').forEach(function(f) {
                    try {
                        if (!f.__term_attached__) {
                            f.__term_attached__ = true;
                            f.contentDocument.addEventListener('keydown', fireBack);
                            f.contentDocument.addEventListener('click', function() {
                                setTimeout(focusChat, 80);
                            });
                        }
                    } catch (ignore) {}
                });
            }
            attachToIframes();
            if (doc.__term_obs__) { try { doc.__term_obs__.disconnect(); } catch(_) {} }
            doc.__term_obs__ = new MutationObserver(attachToIframes);
            doc.__term_obs__.observe(doc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=1,
    )


def _render_market_warnings(snapshot: dict[str, Any]) -> None:
    warnings = snapshot.get("warnings", [])
    omissions = snapshot.get("omissions", [])
    if not warnings and not omissions:
        return
    if warnings:
        message = f"Data notes: {len(warnings)} validation/fetch warning(s). Expand for every detail."
        st.markdown(f"<p class='term-line term-dim'>{html.escape(message)}</p>", unsafe_allow_html=True)
        with st.expander("All market-data warnings"):
            for warning in warnings:
                st.markdown(f"- {html.escape(str(warning))}")
    if omissions:
        with st.expander(f"Omitted contract months ({len(omissions)})", expanded=True):
            rows = []
            for item in omissions:
                rows.append({
                    "delivery": item.get("delivery_month"),
                    "missing contracts": " / ".join(item.get("missing_legs", [])) or "join validation",
                    "reason": item.get("reason"),
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_terminal() -> None:
    st.markdown(TERMINAL_CSS, unsafe_allow_html=True)
    _inject_terminal_js()

    if "term_history" not in st.session_state:
        st.session_state.term_history = [
            {"type": "dim", "text": "type 'help' for commands"},
        ]

    # ── header ────────────────────────────────────────────────────────────────
    col_back, col_title = st.columns([1, 9])
    with col_back:
        if st.button("← Back", key="terminal_back"):
            st.session_state.mode = "newscenter"
            st.rerun()
    with col_title:
        st.markdown(
            "<p style='font-size:1.1em;letter-spacing:0.12em;color:#999 !important;"
            "padding-top:6px;margin:0'>MOOPER TERMINAL</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr style='margin:6px 0 10px'>", unsafe_allow_html=True)

    # ── history ───────────────────────────────────────────────────────────────
    for entry in st.session_state.term_history:
        t = entry["type"]
        if t == "cmd":
            st.markdown(
                f"<p class='term-line'><span class='term-cmd'>▶ {html.escape(str(entry['text']))}</span></p>",
                unsafe_allow_html=True,
            )
        elif t == "text":
            text = html.escape(str(entry["text"])).replace("\n", "<br>")
            st.markdown(
                f"<p class='term-line term-out'>{text}</p>",
                unsafe_allow_html=True,
            )
        elif t == "info":
            st.markdown(
                f"<p class='term-line term-out'>{html.escape(str(entry['text']))}</p>",
                unsafe_allow_html=True,
            )
        elif t == "dim":
            st.markdown(
                f"<p class='term-line term-dim'>{html.escape(str(entry['text']))}</p>",
                unsafe_allow_html=True,
            )
        elif t == "error":
            st.markdown(
                f"<p class='term-line term-err'>error: {html.escape(str(entry['text']))}</p>",
                unsafe_allow_html=True,
            )
        elif t == "chart":
            fig = _build_chart(entry["series"], entry.get("start"), entry.get("end"), entry.get("show_sum", False))
            st.plotly_chart(fig, use_container_width=True)
        elif t == "market_curve":
            snapshot = entry["snapshot"]
            state = "STALE CACHE" if snapshot.get("is_stale") else "VALIDATED"
            max_back = snapshot.get("max_backwardation")
            max_contango = snapshot.get("max_contango")
            structure = (
                f"max backwardation {max_back['label']} ${max_back['spread']:+.2f}" if max_back else "no backwardated pair"
            ) + " · " + (
                f"max contango {max_contango['label']} ${max_contango['spread']:+.2f}" if max_contango else "no contangoed pair"
            )
            summary = (
                f"{state} · {snapshot['source']} · {snapshot['delay']} · "
                f"front ${snapshot['front_price']:.2f} · last ${snapshot['last_price']:.2f} · "
                f"front-to-last ${snapshot['front_to_last']:+.2f}/bbl · {structure}"
            )
            st.markdown(f"<p class='term-line term-out'>{html.escape(summary)}</p>", unsafe_allow_html=True)
            provenance = (
                f"Retrieved {snapshot.get('retrieved_at', 'unknown')} | "
                f"cache={snapshot.get('cache_status', 'unknown')} age={snapshot.get('cache_age_seconds', 0):.1f}s | "
                f"coverage={snapshot.get('returned_months', len(snapshot['quotes']))}/{snapshot.get('requested_months', '?')} months"
            )
            st.caption(provenance)
            st.plotly_chart(_build_curve_chart(snapshot), use_container_width=True)
            st.dataframe(pd.DataFrame([{
                "contract": row["symbol"],
                "delivery": row["delivery_month"],
                "price $/bbl": round(row["price"], 3),
                "volume": row["volume"],
                "quote time UTC": row["quote_time"],
                "last trade date": row.get("last_trade_date"),
                "source URL": row.get("source_url"),
                "quote status": row.get("staleness_note") or "current",
            } for row in snapshot["quotes"]]), hide_index=True, use_container_width=True)
            st.dataframe(pd.DataFrame([{
                "calendar spread": row["label"],
                "front - next $/bbl": round(row["spread"], 3),
                "structure": row["structure"],
            } for row in snapshot["calendar_spreads"]]), hide_index=True, use_container_width=True)
            _render_market_warnings(snapshot)
        elif t == "market_cracks":
            snapshot = entry["snapshot"]
            state = "STALE CACHE" if snapshot.get("is_stale") else "VALIDATED"
            summary = f"{state} · {snapshot['source']} · {snapshot['delay']} · explicit same-month CL/RB/HO legs"
            st.markdown(f"<p class='term-line term-out'>{html.escape(summary)}</p>", unsafe_allow_html=True)
            st.plotly_chart(_build_cracks_chart(snapshot), use_container_width=True)
            provenance = (
                f"Retrieved {snapshot.get('retrieved_at', 'unknown')} | "
                f"cache={snapshot.get('cache_status', 'unknown')} age={snapshot.get('cache_age_seconds', 0):.1f}s | "
                f"coverage={snapshot.get('returned_months', len(snapshot['rows']))}/{snapshot.get('requested_months', '?')} months"
            )
            st.caption(provenance)
            st.dataframe(pd.DataFrame([{
                "delivery": row["delivery_month"],
                "CL $/bbl": round(row["cl"]["price"], 3),
                "RB $/gal": round(row["rb"]["price"], 4),
                "HO $/gal": round(row["ho"]["price"], 4),
                "gasoline $/bbl": round(row["gasoline"], 2),
                "distillate $/bbl": round(row["distillate"], 2),
                "3-2-1 $/bbl": round(row["three_two_one"], 2),
                "latest quote UTC": max(row["cl"]["quote_time"], row["rb"]["quote_time"], row["ho"]["quote_time"]),
                "quote status": "stale leg" if any(row[leg].get("is_stale") for leg in ("cl", "rb", "ho")) else "current",
                "contracts": f"{row['cl']['symbol']} / {row['rb']['symbol']} / {row['ho']['symbol']}",
                "CL quote UTC": row["cl"]["quote_time"],
                "RB quote UTC": row["rb"]["quote_time"],
                "HO quote UTC": row["ho"]["quote_time"],
            } for row in snapshot["rows"]]), hide_index=True, use_container_width=True)
            formula = "Formulas: RB×42−CL · HO×42−CL · ((2×RB×42)+(HO×42))/3−CL"
            st.markdown(f"<p class='term-line term-dim'>{html.escape(formula)}</p>", unsafe_allow_html=True)
            _render_market_warnings(snapshot)

    # blinking cursor
    st.markdown("<p class='term-line'><span class='cursor'>█</span></p>", unsafe_allow_html=True)

    # ── input ─────────────────────────────────────────────────────────────────
    cmd = st.chat_input("command")
    if cmd:
        st.session_state.term_history.append({"type": "cmd", "text": cmd})
        verb = cmd.strip().split()[0].lower() if cmd.strip() else ""
        if verb in ("curve", "cracks", "crack"):
            with st.spinner("Loading and validating explicit futures contracts..."):
                outputs = _execute(cmd)
        else:
            outputs = _execute(cmd)
        st.session_state.term_history.extend(outputs)
        st.rerun()
