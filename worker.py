"""
MediaFlow worker — runs collect + classify on a fixed interval,
and commits a daily data snapshot to GitHub for archival.

Sole writer of MediaFlow data. Launched as a child process of
supervisor.py (the Railway startCommand); not meant to be run as its
own Railway service. Holds an OS-level advisory lock (worker_state.py)
for the duration of each cycle, so a hard-killed cycle releases
immediately via the kernel rather than leaving a permanent stale lock,
and records cycle-state for the dashboard to display as read-only
freshness/health.

Environment variables:
  DATA_DIR               path to shared Railway volume (default: repo dir)
  COLLECT_INTERVAL_SECONDS  collect+classify cadence in seconds (default: 900)
  CLASSIFIER_PROVIDER    anthropic (default), openai, or gemini
  CLASSIFIER_MODEL       optional provider model override
  GITHUB_TOKEN           personal access token for daily snapshot commits
"""

import base64
import os
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import rss_collect
import mediaflow_classify
import worker_state

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE))
INTERVAL = int(os.environ.get("COLLECT_INTERVAL_SECONDS", 900))
POLL_TICK_SECONDS = 5
HEARTBEAT_INTERVAL_SECONDS = 30

GITHUB_REPO_URL = "https://github.com/OwenTanzer/oil-futures.git"

_last_snapshot_date: date | None = None
_git_ready = False


GIT_TIMEOUT_SECONDS = 30


def git_setup() -> bool:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("[snapshot] GITHUB_TOKEN not set — daily snapshots disabled")
        return False
    try:
        subprocess.run(["git", "config", "user.name", "MediaFlow Worker"], cwd=HERE, check=True, timeout=GIT_TIMEOUT_SECONDS)
        subprocess.run(["git", "config", "user.email", "worker@mediaflow.local"], cwd=HERE, check=True, timeout=GIT_TIMEOUT_SECONDS)
        # Deliberately no credentials embedded in the remote URL — that would
        # persist GITHUB_TOKEN in plaintext in .git/config on disk, readable
        # by anything with filesystem access to the container. Auth is
        # instead supplied per-invocation at push time (_authed_push_cmd).
        subprocess.run(
            ["git", "remote", "set-url", "origin", GITHUB_REPO_URL],
            cwd=HERE, check=True, timeout=GIT_TIMEOUT_SECONDS,
        )
        print("[snapshot] git configured")
        return True
    except Exception as e:
        print(f"[snapshot] git setup failed: {e}")
        return False


def _authed_push_cmd() -> list[str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    auth_header = base64.b64encode(f"x-token:{token}".encode()).decode()
    return ["git", "-c", f"http.extraHeader=AUTHORIZATION: basic {auth_header}", "push", "origin", "HEAD"]


def daily_snapshot() -> None:
    """Archival only — must never be able to block the ingestion cycle.
    Every subprocess call is bounded, and the caller (run_cycle) treats any
    exception from this function as non-fatal to the cycle overall."""
    global _last_snapshot_date
    today = date.today()
    if _last_snapshot_date == today:
        return

    snapshot_dir = HERE / "snapshots" / today.isoformat()
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    data_files = [
        "mediaflow_items.json",
        "mediaflow_classified.json",
        "mediaflow_seen.json",
        "mediaflow_log.txt",
    ]
    for fname in data_files:
        src = DATA_DIR / fname
        if src.exists():
            shutil.copy2(src, snapshot_dir / fname)

    add = subprocess.run(
        ["git", "add", f"snapshots/{today.isoformat()}/"],
        cwd=HERE, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
    )
    commit = subprocess.run(
        ["git", "commit", "-m", f"snapshot: {today.isoformat()}"],
        cwd=HERE, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
    )
    if commit.returncode == 0:
        subprocess.run(_authed_push_cmd(), cwd=HERE, check=True, timeout=GIT_TIMEOUT_SECONDS)
        print(f"[snapshot] pushed {today.isoformat()}")
        _last_snapshot_date = today
    elif "nothing to commit" in commit.stdout:
        print(f"[snapshot] nothing new for {today.isoformat()}")
        _last_snapshot_date = today
    else:
        print(f"[snapshot] commit failed: {commit.stderr.strip()}")


def run_cycle() -> None:
    lease = worker_state.try_acquire()
    if lease is None:
        print("[worker] lease held by another owner — skipping this cycle")
        return

    cycle_id = lease.cycle_id
    worker_state.record_cycle_start(cycle_id)
    print(f"[worker] cycle {cycle_id} start")

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            worker_state.heartbeat(lease)

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    started = time.perf_counter()
    try:
        print("--- collect ---")
        items_collected = rss_collect.run()
        print("--- classify ---")
        items_classified = mediaflow_classify.run()

        if _git_ready:
            try:
                daily_snapshot()
            except Exception as e:
                # Archival is best-effort and must never fail the ingestion
                # cycle it rides along with — a stalled `git push` should not
                # look like a stuck collector.
                print(f"[worker] snapshot failed (non-fatal): {e}")

        duration = time.perf_counter() - started
        next_scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=INTERVAL)
        worker_state.record_cycle_success(
            cycle_id, duration, items_collected, items_classified, next_scheduled_at
        )
        print(f"[worker] cycle {cycle_id} success in {duration:.1f}s")
    except Exception as e:
        worker_state.record_cycle_error(cycle_id, str(e))
        print(f"[worker] cycle {cycle_id} FAILED: {e}")
        raise
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        worker_state.release(lease)


if __name__ == "__main__":
    print(f"Worker started. Interval: {INTERVAL}s  DATA_DIR: {DATA_DIR}")
    _git_ready = git_setup()
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"[ERROR] cycle failed: {e}")

        elapsed = 0
        while elapsed < INTERVAL:
            tick = min(POLL_TICK_SECONDS, INTERVAL - elapsed)
            time.sleep(tick)
            elapsed += tick
            if worker_state.consume_refresh_if_pending():
                print("[worker] refresh request received — starting cycle early")
                break
