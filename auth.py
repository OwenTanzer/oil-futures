"""Password gate for the MediaFlow dashboard.

The whole app (dashboard, agent chat, terminal) is a single public Streamlit
service with no other access control — the chat mode spends the owner's
Anthropic API key on every message, so an unauthenticated visitor could run
up arbitrary API cost. require_password() gates all of main() behind a
single shared secret read from MEDIAFLOW_APP_PASSWORD.
"""

from __future__ import annotations

import os
import secrets
import time

import streamlit as st

_PASSWORD_ENV_VAR = "MEDIAFLOW_APP_PASSWORD"
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300

# Per-process, keyed by best-effort client identity. Deliberately not
# per-session: session_state resets on every new browser session, which
# would make the lockout trivially bypassable by just reconnecting.
_failed_attempts: dict[str, list[float]] = {}


def _client_key() -> str:
    try:
        forwarded = st.context.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if st.context.ip_address:
            return st.context.ip_address
    except Exception:
        pass
    return "unknown"


def _lockout_remaining(key: str) -> float:
    now = time.monotonic()
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < _LOCKOUT_SECONDS]
    _failed_attempts[key] = attempts
    if len(attempts) < _MAX_ATTEMPTS:
        return 0
    return _LOCKOUT_SECONDS - (now - attempts[0])


def _record_failure(key: str) -> None:
    _failed_attempts.setdefault(key, []).append(time.monotonic())


def require_password() -> bool:
    """Render a login form if needed. Returns True once authenticated."""
    if st.session_state.get("authenticated"):
        return True

    configured = os.environ.get(_PASSWORD_ENV_VAR, "")
    if not configured:
        st.error(
            f"{_PASSWORD_ENV_VAR} is not set — refusing to start unprotected. "
            "Set it in the deployment environment and restart."
        )
        return False

    key = _client_key()
    remaining = _lockout_remaining(key)

    st.markdown("### MediaFlow — Sign in")

    if remaining > 0:
        st.error(f"Too many attempts. Try again in {int(remaining) // 60 + 1} min.")
        return False

    with st.form("login_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        if secrets.compare_digest(pw.encode(), configured.encode()):
            st.session_state.authenticated = True
            st.rerun()
        else:
            _record_failure(key)
            st.error("Incorrect password.")

    return False
