"""
Shared coordination state for the MediaFlow worker and dashboard.

  .worker.lock           - worker-only; OS-level advisory lock (fcntl.flock on
                            POSIX, msvcrt.locking on Windows) held for the
                            duration of a cycle. This is the actual mutual-
                            exclusion mechanism: the kernel releases it the
                            instant the holding process dies, for any reason
                            (crash, OOM kill, hard kill), so there is no
                            expiry window and no stale-lock failure mode.
  .worker_lease.json      - worker-only; diagnostic mirror of who holds
                            .worker.lock and since when. Never consulted for
                            acquisition decisions - the OS lock is the sole
                            source of truth.
  .refresh_request.json  - dashboard writes, worker consumes
  .cycle_state.json      - worker writes, dashboard reads (freshness/health)

The latter two are always written atomically (temp file + os.replace).
"""

import json
import os
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))

LOCK_FILE = DATA_DIR / ".worker.lock"
LEASE_INFO_FILE = DATA_DIR / ".worker_lease.json"
REFRESH_REQUEST_FILE = DATA_DIR / ".refresh_request.json"
CYCLE_STATE_FILE = DATA_DIR / ".cycle_state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


# ── lease (OS-level advisory lock; JSON file is diagnostics only) ────────────

class Lease:
    def __init__(self, cycle_id: str, acquired_at: datetime, fd: int):
        self.cycle_id = cycle_id
        self.acquired_at = acquired_at
        self.heartbeat_at = acquired_at
        self._fd = fd


def _lock_nonblocking(fd: int) -> bool:
    """Attempt to take an exclusive, non-blocking lock on fd. Returns False
    (never raises) if another process already holds it."""
    if sys.platform == "win32":
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def try_acquire() -> Optional[Lease]:
    """Acquire the worker lock. Returns None if another live process already
    holds it. Correctness comes entirely from the OS: flock/locking is
    atomic against concurrent acquirers and is released by the kernel the
    moment the holding process exits or is killed — there is nothing to
    expire, reclaim, or race on."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
    except OSError:
        pass

    if not _lock_nonblocking(fd):
        os.close(fd)
        return None

    now = _now()
    lease = Lease(cycle_id=new_cycle_id(), acquired_at=now, fd=fd)
    _write_lease_info(lease)
    return lease


def _write_lease_info(lease: Lease) -> None:
    atomic_write_json(LEASE_INFO_FILE, {
        "owner_pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquired_at": _iso(lease.acquired_at),
        "heartbeat_at": _iso(lease.heartbeat_at),
        "cycle_id": lease.cycle_id,
    })


def heartbeat(lease: Lease) -> None:
    """Diagnostic only — updates the info file so logs/operators can see the
    cycle is still alive. Not required for lock correctness."""
    lease.heartbeat_at = _now()
    _write_lease_info(lease)


def release(lease: Lease) -> None:
    try:
        _unlock(lease._fd)
    finally:
        try:
            os.close(lease._fd)
        except OSError:
            pass
    try:
        LEASE_INFO_FILE.unlink(missing_ok=True)
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
