"""
MediaFlow supervisor — Railway entrypoint.

Launches the Streamlit dashboard and worker.py as child processes.
Streamlit exiting brings the whole container down (Railway's own
restartPolicyType handles that). worker.py exiting is restarted in-place
with bounded exponential backoff, since it's the only thing keeping
scheduled collection alive and Railway's container health check only
watches Streamlit's HTTP port.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent

WORKER_RESTART_MIN_DELAY = 5
WORKER_RESTART_MAX_DELAY = 300
WORKER_STABLE_UPTIME_SECONDS = 120

_shutting_down = threading.Event()
_worker_proc: subprocess.Popen | None = None
_streamlit_proc: subprocess.Popen | None = None


def _streamlit_cmd() -> list[str]:
    port = os.environ.get("PORT", "8501")
    return [
        sys.executable, "-m", "streamlit", "run", "mediaflow_app.py",
        "--server.port", port,
        "--server.address", "0.0.0.0",
    ]


def _worker_cmd() -> list[str]:
    return [sys.executable, "worker.py"]


def _spawn(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=HERE, env=os.environ.copy())


def _terminate(proc: subprocess.Popen, timeout: float = 10) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _watch_streamlit() -> None:
    global _streamlit_proc
    _streamlit_proc = _spawn(_streamlit_cmd())
    print(f"[supervisor] streamlit started (pid={_streamlit_proc.pid})", flush=True)
    code = _streamlit_proc.wait()
    if _shutting_down.is_set():
        return
    print(f"[supervisor] streamlit exited with code {code} — shutting down container", flush=True)
    if _worker_proc is not None:
        _terminate(_worker_proc)
    _shutting_down.set()
    sys.exit(code or 1)


def _watch_worker() -> None:
    global _worker_proc
    restart_count = 0

    while not _shutting_down.is_set():
        _worker_proc = _spawn(_worker_cmd())
        print(f"[supervisor] worker started (pid={_worker_proc.pid})", flush=True)
        started_at = time.monotonic()
        code = _worker_proc.wait()

        if _shutting_down.is_set():
            return

        uptime = time.monotonic() - started_at
        if uptime >= WORKER_STABLE_UPTIME_SECONDS:
            restart_count = 0
        else:
            restart_count += 1

        delay = min(WORKER_RESTART_MAX_DELAY, WORKER_RESTART_MIN_DELAY * (2 ** min(restart_count, 6)))
        print(
            f"[supervisor] worker exited (code={code}, uptime={uptime:.0f}s) — "
            f"restarting in {delay}s (attempt {restart_count})",
            flush=True,
        )
        _shutting_down.wait(delay)


def _handle_signal(signum, frame) -> None:
    print(f"[supervisor] received signal {signum} — shutting down children", flush=True)
    _shutting_down.set()
    if _streamlit_proc is not None:
        _terminate(_streamlit_proc)
    if _worker_proc is not None:
        _terminate(_worker_proc)
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    worker_thread = threading.Thread(target=_watch_worker, daemon=True)
    worker_thread.start()

    # Streamlit runs on the main thread so sys.exit() from _watch_streamlit
    # actually terminates the process.
    _watch_streamlit()


if __name__ == "__main__":
    main()
