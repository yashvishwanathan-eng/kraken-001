from worker.detectors import torso_angle_deg


def person(shoulder, hip, conf=0.9):
    kp = [(0.0, 0.0, 0.0)] * 17
    kp[5] = kp[6] = (shoulder[0], shoulder[1], conf)
    kp[11] = kp[12] = (hip[0], hip[1], conf)
    return kp


def test_upright_torso_is_zero_degrees():
    assert torso_angle_deg(person((100, 100), (100, 300))) == 0


def test_lying_flat_is_about_ninety_degrees():
    assert round(torso_angle_deg(person((100, 300), (300, 300)))) == 90


def test_bending_is_between_upright_and_flat():
    angle = torso_angle_deg(person((100, 150), (160, 300)))
    assert 15 < angle < 30


def test_hidden_keypoints_return_none():
    assert torso_angle_deg(person((100, 100), (100, 300), conf=0.1)) is None
