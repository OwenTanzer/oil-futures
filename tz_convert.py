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
