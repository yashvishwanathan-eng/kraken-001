"""P22. Start and stop camera workers from the API ("install" a camera without opening a new window).

Each camera still runs in its own process, exactly as by hand:
    python -m worker.run_worker --camera <id>
The API only launches that process, keeps a handle on it and writes its output to logs/worker-<id>.log.
Detection never runs inside the API.

With WORKERS_AUTOSTART=true the API starts every enabled camera when it boots and restarts a worker that
crashed (after WORKER_RESTART_S). Without it, workers start when an admin adds a camera with start=true or
presses Start, and a crashed worker stays stopped until someone starts it again.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import psutil

from app.config import BACKEND_DIR, get_settings

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console window pops up per worker

log = logging.getLogger("safetyai.workers")


def stray_workers(camera_id: str, keep_pid: int | None = None) -> list[psutil.Process]:
    """Worker processes for this camera that the API does not hold: left over from an earlier API run, or started
    by hand. Two workers on one camera fight over the webcam and the CPU (and the second exits with code 1)."""
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if proc.info["pid"] in (keep_pid, os.getpid()) or "worker.run_worker" not in cmd:
            continue
        if "--camera" in cmd and cmd.index("--camera") + 1 < len(cmd) and cmd[cmd.index("--camera") + 1] == camera_id:
            found.append(proc)
    return found


def stop_strays(camera_id: str, keep_pid: int | None = None) -> int:
    procs = stray_workers(camera_id, keep_pid)
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for proc in alive:
        proc.kill()
    if procs:
        log.warning("camera=%s stopped %d leftover worker(s): %s", camera_id, len(procs), [p.pid for p in procs])
    return len(procs)


@dataclass
class WorkerProcess:
    camera_id: str
    process: subprocess.Popen
    started_at: datetime
    wanted: bool = True  # False once someone pressed Stop: do not restart it
    exited_at: float | None = None  # monotonic time the crash was noticed


class WorkerSupervisor:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerProcess] = {}
        self._lock = threading.Lock()

    def _log_path(self, camera_id: str):
        folder = BACKEND_DIR / get_settings().worker_log_dir
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"worker-{camera_id}.log"

    def _launch(self, camera_id: str) -> subprocess.Popen:
        log_file = open(self._log_path(camera_id), "ab")  # the child keeps its own copy of the handle
        try:
            return subprocess.Popen([sys.executable, "-m", "worker.run_worker", "--camera", camera_id,
                                     "--parent-pid", str(os.getpid())],
                                    cwd=BACKEND_DIR, stdout=log_file, stderr=subprocess.STDOUT,
                                    creationflags=NO_WINDOW)
        finally:
            log_file.close()

    def start(self, camera_id: str) -> bool:
        """Start the worker. False if it is already running."""
        with self._lock:
            current = self._workers.get(camera_id)
            if current is not None and current.process.poll() is None:
                current.wanted = True
                return False
            stop_strays(camera_id)  # an old worker would hold the webcam and make this one exit with code 1
            process = self._launch(camera_id)
            self._workers[camera_id] = WorkerProcess(camera_id, process, datetime.now(timezone.utc))
        log.info("camera=%s worker started pid=%d", camera_id, process.pid)
        return True

    def stop(self, camera_id: str, timeout_s: float = 5.0) -> bool:
        """Stop the worker (and any leftover one for this camera). False if none was running."""
        with self._lock:
            current = self._workers.get(camera_id)
            if current is not None:
                current.wanted = False
        strays = stop_strays(camera_id, current.process.pid if current is not None else None)
        if current is None or current.process.poll() is not None:
            return strays > 0
        current.process.terminate()
        try:
            current.process.wait(timeout_s)
        except subprocess.TimeoutExpired:
            current.process.kill()
        log.info("camera=%s worker stopped", camera_id)
        return True

    def restart(self, camera_id: str) -> None:
        self.stop(camera_id)
        self.start(camera_id)

    def status(self, camera_id: str) -> dict:
        """{managed, running, pid, started_at, exit_code}. managed=False: started by hand (or not at all)."""
        with self._lock:
            current = self._workers.get(camera_id)
        if current is None or current.process.poll() is not None:
            stray = next(iter(stray_workers(camera_id)), None)
            if stray is not None:  # running outside the API: Stop and Restart deal with it
                return {"managed": False, "running": True, "pid": stray.pid, "started_at": None, "exit_code": None}
        if current is None:
            return {"managed": False, "running": False, "pid": None, "started_at": None, "exit_code": None}
        code = current.process.poll()
        return {"managed": True, "running": code is None, "pid": current.process.pid,
                "started_at": current.started_at, "exit_code": code}

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for w in self._workers.values() if w.process.poll() is None)

    def log_tail(self, camera_id: str, lines: int = 40) -> list[str]:
        path = self._log_path(camera_id)
        if not path.exists():
            return []
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16_000))
            text = f.read().decode("utf-8", errors="replace")
        return text.splitlines()[-lines:]

    def check(self) -> None:
        """Called every few seconds: restart crashed workers when autostart is on."""
        settings = get_settings()
        if not settings.workers_autostart:
            return
        now = time.monotonic()
        restart = []
        with self._lock:
            for worker in self._workers.values():
                if not worker.wanted or worker.process.poll() is None:
                    continue
                if worker.exited_at is None:
                    worker.exited_at = now
                    log.warning("camera=%s worker exited with code %s; restarting in %.0f s", worker.camera_id,
                                worker.process.returncode, settings.worker_restart_s)
                elif now - worker.exited_at >= settings.worker_restart_s:
                    restart.append(worker.camera_id)
        for camera_id in restart:
            self.start(camera_id)

    def start_enabled(self) -> int:
        """At API startup with WORKERS_AUTOSTART=true: start every enabled camera."""
        from app.services.camera_registry import all_cameras

        return sum(1 for cam in all_cameras(enabled_only=True) if self.start(cam.id))

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._workers)
        for camera_id in ids:
            self.stop(camera_id)


workers = WorkerSupervisor()
