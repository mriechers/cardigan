"""System management endpoints.

Provides status and restart controls for system components:
- API server
- Worker process
- Transcript watcher
"""

import os
import subprocess
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services import database

router = APIRouter()


class ComponentStatus(BaseModel):
    """Status of a system component."""

    name: str
    running: bool
    pid: Optional[int] = None
    # Seconds since the component's last DB heartbeat (None for API, which is
    # detected by port, or if the component has never heartbeated).
    heartbeat_age_seconds: Optional[float] = None


class SystemStatus(BaseModel):
    """Status of all system components."""

    api: ComponentStatus
    worker: ComponentStatus
    watcher: ComponentStatus


class RestartResponse(BaseModel):
    """Response from restart operation."""

    success: bool
    message: str


def _find_process(pattern: str) -> Optional[int]:
    """Find PID of process matching pattern."""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            # Return first matching PID
            pids = result.stdout.strip().split("\n")
            return int(pids[0])
        return None
    except Exception:
        return None


def _check_port_in_use(port: int) -> Optional[int]:
    """Check if a port is in use and return the PID using it.

    More reliable than pgrep for detecting running servers,
    especially when the server process is the one calling this function.
    """
    try:
        result = subprocess.run(["lsof", "-t", "-i", f":{port}"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            # Return first PID using the port
            pids = result.stdout.strip().split("\n")
            return int(pids[0])
        return None
    except Exception:
        return None


def _kill_process(pattern: str) -> bool:
    """Kill process matching pattern."""
    try:
        result = subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _start_component(command: str, log_file: str) -> bool:
    """Start a component in background."""
    try:
        project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        log_path = os.path.join(project_dir, "logs", log_file)

        # Ensure logs directory exists
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        with open(log_path, "a") as log:
            subprocess.Popen(command, shell=True, cwd=project_dir, stdout=log, stderr=log, start_new_session=True)
        return True
    except Exception:
        return False


@router.get("/status", response_model=SystemStatus)
async def get_system_status():
    """Get status of all system components.

    A component is reported running if EITHER a fresh DB heartbeat exists
    (works across container boundaries — the prod/LXC case, #179) OR a local
    process is found via pgrep/lsof (works in single-host dev). The OR means
    neither deployment shape reports a false "down".
    """
    # API check: port probe — the API answers its own request in-container.
    api_pid = _check_port_in_use(8000)

    # Worker/watcher: same-host process probe (dev) plus shared-DB heartbeat (prod).
    worker_pid = _find_process("run_worker.py")
    watcher_pid = _find_process("watch_transcripts.py")
    worker_age = await database.get_heartbeat_age_seconds("worker")
    watcher_age = await database.get_heartbeat_age_seconds("watcher")

    worker_running = worker_pid is not None or database.heartbeat_is_fresh(worker_age)
    watcher_running = watcher_pid is not None or database.heartbeat_is_fresh(watcher_age)

    return SystemStatus(
        api=ComponentStatus(name="API Server", running=api_pid is not None, pid=api_pid),
        worker=ComponentStatus(name="Worker", running=worker_running, pid=worker_pid, heartbeat_age_seconds=worker_age),
        watcher=ComponentStatus(
            name="Transcript Watcher",
            running=watcher_running,
            pid=watcher_pid,
            heartbeat_age_seconds=watcher_age,
        ),
    )


@router.post("/watcher/heartbeat", response_model=RestartResponse)
async def watcher_heartbeat():
    """Record a liveness heartbeat for the transcript watcher.

    The watcher runs in its own container and reaches the API over HTTP (it has
    no direct DB access), so it pings this endpoint each scan loop. /status reads
    the resulting DB heartbeat to detect the watcher across container boundaries.
    """
    await database.record_heartbeat("watcher")
    return RestartResponse(success=True, message="Watcher heartbeat recorded")


@router.post("/worker/restart", response_model=RestartResponse)
async def restart_worker():
    """Restart the worker process."""

    # Kill existing worker
    _kill_process("run_worker.py")

    # Start new worker
    success = _start_component("./venv/bin/python run_worker.py", "worker.log")

    if success:
        return RestartResponse(success=True, message="Worker restarted successfully")
    else:
        raise HTTPException(status_code=500, detail="Failed to restart worker")


@router.post("/watcher/restart", response_model=RestartResponse)
async def restart_watcher():
    """Restart the transcript watcher."""

    # Kill existing watcher
    _kill_process("watch_transcripts.py")

    # Start new watcher
    success = _start_component("./venv/bin/python watch_transcripts.py", "watcher.log")

    if success:
        return RestartResponse(success=True, message="Watcher restarted successfully")
    else:
        raise HTTPException(status_code=500, detail="Failed to restart watcher")


@router.post("/worker/stop", response_model=RestartResponse)
async def stop_worker():
    """Stop the worker process."""

    if _kill_process("run_worker.py"):
        return RestartResponse(success=True, message="Worker stopped")
    else:
        return RestartResponse(success=False, message="Worker was not running")


@router.post("/watcher/stop", response_model=RestartResponse)
async def stop_watcher():
    """Stop the transcript watcher."""

    if _kill_process("watch_transcripts.py"):
        return RestartResponse(success=True, message="Watcher stopped")
    else:
        return RestartResponse(success=False, message="Watcher was not running")


@router.post("/worker/start", response_model=RestartResponse)
async def start_worker():
    """Start the worker process if not running."""

    if _find_process("run_worker.py"):
        return RestartResponse(success=False, message="Worker is already running")

    success = _start_component("./venv/bin/python run_worker.py", "worker.log")

    if success:
        return RestartResponse(success=True, message="Worker started")
    else:
        raise HTTPException(status_code=500, detail="Failed to start worker")


@router.post("/watcher/start", response_model=RestartResponse)
async def start_watcher():
    """Start the transcript watcher if not running."""

    if _find_process("watch_transcripts.py"):
        return RestartResponse(success=False, message="Watcher is already running")

    success = _start_component("./venv/bin/python watch_transcripts.py", "watcher.log")

    if success:
        return RestartResponse(success=True, message="Watcher started")
    else:
        raise HTTPException(status_code=500, detail="Failed to start watcher")
