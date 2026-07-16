"""
Shared coordination state for the MediaFlow worker and dashboard.

Three small JSON files under DATA_DIR, always written atomically
(temp file + os.replace):
  .worker_lease.json    - worker-only; crash-safe lease with expiry-based reclaim
  .refresh_request.json - dashboard writes, worker consumes
  .cycle_state.json     - worker writes, dashboard reads (freshness/health display)
"""

import json
import os
import socket
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))

LEASE_FILE = DATA_DIR / ".worker_lease.json"
REFRESH_REQUEST_FILE = DATA_DIR / ".refresh_request.json"
CYCLE_STATE_FILE = DATA_DIR / ".cycle_state.json"

LEASE_TTL_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def new_cycle_id() -> str:
    return f"{_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


# ── lease ────────────────────────────────────────────────────────────────────

class Lease:
    def __init__(self, cycle_id: str, acquired_at: datetime):
        self.cycle_id = cycle_id
        self.acquired_at = acquired_at
        self.heartbeat_at = acquired_at


def try_acquire() -> Optional[Lease]:
    """Acquire the worker lease. Returns None if another owner currently holds
    a non-expired lease. A missing, corrupt, or expired lease is always
    reclaimable — expiry is the sole correctness mechanism, not cleanup."""
    existing = _read_json(LEASE_FILE)
    now = _now()

    if existing is not None:
        try:
            expires_at = _parse_iso(existing["expires_at"])
        except (KeyError, ValueError):
            expires_at = None

        if expires_at is not None and now < expires_at:
            return None

        if expires_at is not None:
            age = (now - expires_at).total_seconds()
            print(
                f"[worker_state] reclaimed expired lease "
                f"(expired {age:.0f}s ago, previous owner pid={existing.get('owner_pid')})",
                flush=True,
            )

    cycle_id = new_cycle_id()
    lease = Lease(cycle_id=cycle_id, acquired_at=now)
    _write_lease(lease)
    return lease


def _write_lease(lease: Lease) -> None:
    expires_at = lease.heartbeat_at + timedelta(seconds=LEASE_TTL_SECONDS)
    atomic_write_json(LEASE_FILE, {
        "owner_pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquired_at": _iso(lease.acquired_at),
        "heartbeat_at": _iso(lease.heartbeat_at),
        "expires_at": _iso(expires_at),
        "cycle_id": lease.cycle_id,
    })


def heartbeat(lease: Lease) -> None:
    lease.heartbeat_at = _now()
    _write_lease(lease)


def release(lease: Lease) -> None:
    try:
        LEASE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── refresh request ─────────────────────────────────────────────────────────

def request_refresh(requested_by: str = "dashboard") -> None:
    atomic_write_json(REFRESH_REQUEST_FILE, {
        "requested_at": _iso(_now()),
        "requested_by": requested_by,
    })


def load_refresh_request() -> Optional[dict]:
    return _read_json(REFRESH_REQUEST_FILE)


def consume_refresh_if_pending() -> bool:
    """Worker-side: if a refresh request is pending, delete it and return True."""
    if REFRESH_REQUEST_FILE.exists():
        try:
            REFRESH_REQUEST_FILE.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    return False


# ── cycle state ──────────────────────────────────────────────────────────────

def load_cycle_state() -> Optional[dict]:
    return _read_json(CYCLE_STATE_FILE)


def record_cycle_start(cycle_id: str) -> None:
    state = load_cycle_state() or {}
    state["last_start"] = _iso(_now())
    state["cycle_id"] = cycle_id
    atomic_write_json(CYCLE_STATE_FILE, state)


def record_cycle_success(
    cycle_id: str,
    duration_seconds: float,
    items_collected: int,
    items_classified: int,
    next_scheduled_at: datetime,
) -> None:
    state = load_cycle_state() or {}
    now_iso = _iso(_now())
    state.update({
        "cycle_id": cycle_id,
        "last_success": now_iso,
        "last_error": None,
        "duration_seconds": round(duration_seconds, 1),
        "items_collected": items_collected,
        "items_classified": items_classified,
        "next_scheduled_at": _iso(next_scheduled_at),
    })
    atomic_write_json(CYCLE_STATE_FILE, state)


def record_cycle_error(cycle_id: str, message: str) -> None:
    state = load_cycle_state() or {}
    state["cycle_id"] = cycle_id
    state["last_error"] = message[:500]
    atomic_write_json(CYCLE_STATE_FILE, state)
