"""
Shared browser-side timezone conversion.

The server never guesses the viewer's timezone. It emits an element carrying a
machine-readable UTC instant:

    <span data-utc="2026-08-04T12:03:00+00:00">Aug 4, 2026, 8:03 AM EDT</span>

and the script below rewrites the element's text into the viewer's local zone.
If the script never runs (JS disabled, iframe blocked, malformed instant), the
server-rendered text stays exactly as written — so the fallback is always a
correct-but-non-local timestamp, never a blank or a wrong one.

Lives in its own module because both mediaflow_app.py and the sub-views need it,
and the sub-views are imported *by* mediaflow_app.py — importing it back would
be a cycle. mediaflow_app.py's newscenter additionally layers a periodic page
reload on top of this; that reload is deliberately NOT part of the shared
converter, since forcing a reload inside the chat or a report view would
interrupt the user mid-read.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit.components.v1 as components

# Kept identical to the format the newscenter has always rendered, so the same
# timestamp reads the same way in every view.
CONVERTER_JS = """
(function() {
    var doc = window.parent.document;
    function convertTimestamps() {
        try {
            doc.querySelectorAll('[data-utc]').forEach(function(el) {
                var utc = el.getAttribute('data-utc');
                if (!utc || el.getAttribute('data-converted')) return;
                var dt = new Date(utc);
                if (isNaN(dt)) return;          // leave the server text alone
                el.textContent = dt.toLocaleString('en-US', {
                    month: 'short', day: 'numeric',
                    hour: '2-digit', minute: '2-digit',
                    timeZoneName: 'short'
                });
                el.setAttribute('data-converted', '1');
            });
        } catch(e) {}
    }
    convertTimestamps();
    // Streamlit re-renders this iframe on every rerun. Without disconnecting
    // the previous observer, each rerun stacks another one on the same parent
    // document, all doing the same full-document scan on every mutation.
    if (doc.__tz_obs__) { try { doc.__tz_obs__.disconnect(); } catch(_) {} }
    doc.__tz_obs__ = new MutationObserver(convertTimestamps);
    doc.__tz_obs__.observe(doc.body, { childList: true, subtree: true });
})();
"""


def inject_converter() -> None:
    """Timezone conversion only — no page-reload timer."""
    components.html(f"<script>{CONVERTER_JS}</script>", height=1)


# ── server half: display string → unambiguous instant ─────────────────────────
#
# Both source docs (HFW and HSN) are hand-maintained Google Docs whose headings
# carry a human timestamp ("Aug 4, 2026, 8:03 AM EDT"). To render that in the
# viewer's zone the server must first pin it to a real instant. Every step below
# fails closed: if we cannot be *confident*, we return None and the caller shows
# the doc's original string. A timestamp left in the author's zone is merely
# inconvenient; one silently shifted to the wrong zone is a false reading on a
# crisis-monitoring instrument.

DEFAULT_SOURCE_TZ = "America/New_York"

# Abbreviation → fixed offset. Fixed offsets rather than ZoneInfo because the
# abbreviation *already* encodes the offset: "EDT" means UTC-4 whether or not
# the given date actually falls in DST, so honouring what the doc says beats
# re-deriving it. Deliberately excludes ambiguous abbreviations — AST is both
# Atlantic (UTC-4) and Arabia (UTC+3), CST is US Central, China and Cuba, IST is
# India, Israel and Ireland, BST is Britain and Bangladesh, CDT is US Central
# and Cuba. On a Gulf-focused dashboard, guessing any of those is exactly the
# silent multi-hour error this table exists to avoid; unknown tokens fall
# through and the raw string is shown instead.
TZ_ABBREVIATIONS = {
    "UTC": 0, "GMT": 0, "Z": 0, "ZULU": 0,
    "EST": -5, "EDT": -4,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8,
    "HST": -10,
    "CET": 1, "CEST": 2, "EET": 2, "EEST": 3,
}

# Trailing "UTC+4", "GMT-0500", "+03:00", "Z" …
NUMERIC_TZ_RE = re.compile(
    r"(?i)(?:\b(?:UTC|GMT)\s*)?([+-])(\d{1,2})(?::?(\d{2}))?\s*$"
)
TRAILING_WORD_RE = re.compile(r"(?i)\s*\(?\b([A-Z]{1,4})\b\)?\.?\s*$")

# Tried in order against the datetime text once the zone token is stripped.
# Covers both live templates: HSN/HFW's "Aug 4, 2026, 8:03 AM" and the older
# HFW "2026-07-22 12:22".
_TS_FORMATS = (
    "%b %d, %Y, %I:%M %p", "%B %d, %Y, %I:%M %p",
    "%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p",
    "%b %d, %Y, %H:%M", "%B %d, %Y, %H:%M",
    "%b %d, %Y %H:%M", "%B %d, %Y %H:%M",
    "%d %b %Y, %I:%M %p", "%d %B %Y, %I:%M %p",
    "%d %b %Y %H:%M", "%d %B %Y %H:%M",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M",
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M",
)

# Both doc families use the same YYYYMMDD-HHMM report-ID scheme, but carry the
# prefix differently: node_status strips it ("20260804-0803") while
# breaking_news keeps it ("HFW-20260712-1516"). Accept either.
REPORT_ID_TS_RE = re.compile(r"^(?:[A-Za-z]+-)?(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$")


def resolve_source_tz(env_var: str, default: str = DEFAULT_SOURCE_TZ):
    """Zone used when a doc gives a date/time with no zone, and for the
    report-ID fallback. Report IDs are the author's local wall clock
    (HSN-20260804-0803 ↔ "8:03 AM EDT"), so this must track wherever the doc is
    written. Each view passes its own env var name so the two docs can move
    independently.
    """
    try:
        return ZoneInfo(os.environ.get(env_var, default))
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # A bad env value must not take the view down; UTC is wrong-but-stated
        # rather than crashing, and the raw string is still rendered alongside.
        return timezone.utc


def _split_zone(text: str) -> tuple[str, timezone | None, bool]:
    """Split trailing zone info off a timestamp.

    Returns (remainder, tzinfo, saw_unknown_zone_token). The third value
    distinguishes "no zone given" (safe to assume the source zone) from "a zone
    was given but we don't recognise it" (never guess — show the raw string).
    """
    text = text.strip().rstrip(".").strip()

    m = NUMERIC_TZ_RE.search(text)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours, minutes = int(m.group(2)), int(m.group(3) or 0)
        if hours <= 14 and minutes < 60:
            offset = timezone(sign * timedelta(hours=hours, minutes=minutes))
            return text[: m.start()].strip().rstrip(",").strip(), offset, False

    m = TRAILING_WORD_RE.search(text)
    if m:
        token = m.group(1).upper()
        if token in TZ_ABBREVIATIONS:
            offset = timezone(timedelta(hours=TZ_ABBREVIATIONS[token]))
            return text[: m.start()].strip().rstrip(",").strip(), offset, False
        # A bare alphabetic token that isn't a month/AM/PM fragment is almost
        # certainly a zone we don't know. Flag it rather than parsing the rest
        # and silently attaching the wrong offset.
        if token not in ("AM", "PM") and not token.isdigit():
            return text[: m.start()].strip().rstrip(",").strip(), None, True

    return text, None, False


def parse_doc_timestamp(
    timestamp_display: str,
    report_id: str = "",
    source_tz=None,
) -> datetime | None:
    """Best-effort UTC instant for a report heading, or None if not confident.

    `source_tz` is the zone to assume when the doc states none; pass the result
    of resolve_source_tz(). Defaults to DEFAULT_SOURCE_TZ when omitted.
    """
    if source_tz is None:
        source_tz = ZoneInfo(DEFAULT_SOURCE_TZ)

    remainder, tz, unknown_zone = _split_zone(timestamp_display or "")

    if not unknown_zone and remainder:
        normalised = re.sub(r"\s+", " ", remainder).strip()
        for fmt in _TS_FORMATS:
            try:
                dt = datetime.strptime(normalised, fmt)
            except ValueError:
                continue
            return dt.replace(tzinfo=tz or source_tz).astimezone(timezone.utc)

    # Heading unparseable (or its zone unrecognised): fall back to the report ID,
    # which is the same wall-clock moment in the source zone. Only safe when the
    # heading gave no zone we disagreed with — if it named a zone we don't know,
    # the ID's source-zone assumption is just as likely to be wrong, so bail.
    if unknown_zone:
        return None

    m = REPORT_ID_TS_RE.match((report_id or "").strip())
    if m:
        y, mo, d, hh, mm = (int(g) for g in m.groups())
        try:
            dt = datetime(y, mo, d, hh, mm, tzinfo=source_tz)
        except ValueError:
            return None
        return dt.astimezone(timezone.utc)

    return None
