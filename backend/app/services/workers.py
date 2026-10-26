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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("safetyai.workers")


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
            return subprocess.Popen([sys.executable, "-m", "worker.run_worker", "--camera", camera_id],
                                    cwd=BACKEND_DIR, stdout=log_file, stderr=subprocess.STDOUT)
        finally:
            log_file.close()

    def start(self, camera_id: str) -> bool:
        """Start the worker. False if it is already running."""
        with self._lock:
            current = self._workers.get(camera_id)
            if current is not None and current.process.poll() is None:
                current.wanted = True
                return False
            process = self._launch(camera_id)
            self._workers[camera_id] = WorkerProcess(camera_id, process, datetime.now(timezone.utc))
        log.info("camera=%s worker started pid=%d", camera_id, process.pid)
        return True

    def stop(self, camera_id: str, timeout_s: float = 5.0) -> bool:
        """Stop the worker. False if it was not running."""
        with self._lock:
            current = self._workers.get(camera_id)
            if current is None:
                return False
            current.wanted = False
        if current.process.poll() is not None:
            return False
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
