"""An old worker left over from an earlier API run must not keep the camera: Start/Stop find and stop it."""

import subprocess
import sys
import time

from app.services.workers import stop_strays, stray_workers


def test_a_leftover_worker_for_the_camera_is_found_and_stopped():
    # looks like "python -m worker.run_worker --camera cam-old" to the process list, but only sleeps
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                            "worker.run_worker", "--camera", "cam-old"])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                              "worker.run_worker", "--camera", "cam-other"])
    try:
        time.sleep(0.3)
        assert [p.pid for p in stray_workers("cam-old")] == [old.pid]
        assert stop_strays("cam-old") == 1
        assert old.wait(5) is not None
        assert other.poll() is None  # another camera's worker is left alone
    finally:
        for p in (old, other):
            p.kill()


def test_a_worker_stops_when_the_api_that_started_it_is_gone():
    api = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    worker = subprocess.Popen([sys.executable, "-c",
                               "import time; from worker.run_worker import watch_parent; "
                               f"watch_parent({api.pid}, every_s=0.2); time.sleep(60)"])
    try:
        time.sleep(1.5)
        assert worker.poll() is None  # the API is alive: keep running
        api.kill()
        api.wait(5)
        assert worker.wait(5) == 0  # the API is gone: the worker stops by itself
    finally:
        for p in (api, worker):
            p.kill()
