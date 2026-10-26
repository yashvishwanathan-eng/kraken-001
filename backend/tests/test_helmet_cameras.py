from worker.run_worker import helmet_camera


def test_empty_list_means_every_camera():
    assert helmet_camera("cam-floor", "")


def test_only_listed_gate_cameras():
    assert helmet_camera("cam-gate", "cam-gate, cam-gate-2")
    assert helmet_camera("cam-gate-2", "cam-gate, cam-gate-2")
    assert not helmet_camera("cam-floor", "cam-gate, cam-gate-2")
