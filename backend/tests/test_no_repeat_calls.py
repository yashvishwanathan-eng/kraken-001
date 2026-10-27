"""P40: only fire, smoke and restricted entry ring phones; one emergency = one ladder; nobody is rung twice."""

from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from app.config import Settings, get_settings
from app.models import Call
from app.services.calls import CallService
from app.services.escalation import Escalation, ladder_for
from tests.conftest import make_incident
from tests.test_escalation import FakeScheduler, timeline
from worker.rules import DetectionObs, RuleConfig, RulesEngine
from worker.verify import flame_fraction, grey_fraction, verify_fire_smoke

W, H = 640, 480
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
FIRE = DetectionObs((400, 300, 480, 420), "fire", 0.7)


# ---------- which types may call ----------

def test_default_only_fire_smoke_and_restricted_entry_call():
    s = Settings(_env_file=None, call_types="fire,smoke,access")
    for kind, severity in (("fire", "critical"), ("smoke", "high"), ("access", "high")):
        assert any(st.action == "call" for st in ladder_for(severity, s, kind))
    for kind, severity in (("fall", "critical"), ("unattended", "high"), ("crowding", "high"),
                           ("ppe", "low"), ("loitering", "low"), ("no_helmet", "low")):
        steps = ladder_for(severity, s, kind)
        assert [st.action for st in steps] == ["whatsapp"], kind  # dashboard + WhatsApp, never a call


@pytest.fixture
def ladder(roster, simulate):
    settings = get_settings()
    return Escalation(scheduler=FakeScheduler(), call_service=CallService(settings),
                      messenger=simulate, settings=settings)


def test_a_fall_is_never_called_when_calls_are_only_for_fire(ladder, roster, monkeypatch):
    monkeypatch.setattr(ladder.settings, "call_types", "fire,smoke,access")
    incident_id = make_incident(roster, type_="fall", severity="critical")
    ladder.start(incident_id)
    for job_id in list(ladder.scheduler.jobs):
        ladder.scheduler.run(job_id)
    roster.expire_all()
    assert roster.query(Call).filter(Call.incident_id == incident_id).count() == 0
    assert ("whatsapp_sent", "Anita Rao") in timeline(roster, incident_id)


# ---------- one emergency, one ladder ----------

def test_second_fire_on_the_same_camera_calls_nobody(ladder, roster):
    first = make_incident(roster, type_="fire", severity="critical")
    ladder.start(first)
    assert ladder.scheduler.jobs
    ladder.scheduler.jobs.clear()
    again = make_incident(roster, type_="fire", severity="critical", minutes_ago=0)
    smoke = make_incident(roster, type_="smoke", severity="high", minutes_ago=0)
    ladder.start(again)
    ladder.start(smoke)
    assert ladder.scheduler.jobs == {}  # nothing scheduled: no calls, no WhatsApp
    assert ("repeat_of", f"#{first}") in timeline(roster, again)
    assert ("repeat_of", f"#{again}") in timeline(roster, smoke)


def test_after_the_fire_is_resolved_a_new_fire_calls_again(ladder, roster):
    first = make_incident(roster, type_="fire", severity="critical", status="resolved")
    again = make_incident(roster, type_="fire", severity="critical", minutes_ago=0)
    ladder.start(again)
    assert ladder.scheduler.jobs
    assert ("repeat_of", f"#{first}") not in timeline(roster, again)


def test_fire_on_another_camera_is_its_own_emergency(ladder, roster):
    make_incident(roster, camera_id="cam-webcam", type_="fire", severity="critical")
    other = make_incident(roster, camera_id="cam-phone", type_="fire", severity="critical", minutes_ago=0)
    ladder.start(other)
    assert ladder.scheduler.jobs


def test_the_same_phone_is_not_rung_twice_for_one_camera(roster, simulate):
    service = CallService(get_settings())
    a = make_incident(roster, type_="fire", severity="critical")
    b = make_incident(roster, type_="access", severity="high", minutes_ago=0)
    other_camera = make_incident(roster, camera_id="cam-phone", type_="fire", severity="critical", minutes_ago=0)
    assert service.place(kind="alert", to_name="Ravi", to_phone="+910000000001", incident_id=a) is not None
    assert service.place(kind="alert", to_name="Ravi", to_phone="+910000000001", incident_id=b) is None
    assert service.place(kind="alert", to_name="Ravi", to_phone="+910000000001", incident_id=other_camera) is not None
    assert service.place(kind="alert", to_name="Anita", to_phone="+910000000002", incident_id=b) is not None


# ---------- the camera worker raises one alarm per event ----------

def run_fire(engine, start, seconds, seen=True, step=0.5):
    fired, t = [], start
    while t < start + seconds:
        fired += [round(t, 1) for c in engine.update(t, NOW, W, H, [], fire=[FIRE] if seen else []) if c.type == "fire"]
        t += step
    return fired


def test_a_fire_that_stays_in_view_raises_one_alarm_not_one_per_cooldown():
    engine = RulesEngine([], RuleConfig(cooldown_s=10, rearm_quiet_s=20, fire_frames=3))
    assert len(run_fire(engine, 0, 120)) == 1  # two minutes of fire, cooldown 10 s: still one alarm


def test_the_fire_alarms_again_only_after_it_was_gone():
    engine = RulesEngine([], RuleConfig(cooldown_s=10, rearm_quiet_s=20, fire_frames=3))
    assert len(run_fire(engine, 0, 30)) == 1
    assert run_fire(engine, 30, 10, seen=False) == []
    assert len(run_fire(engine, 40, 10)) == 0  # gone for 10 s only: same event
    assert run_fire(engine, 50, 25, seen=False) == []
    assert len(run_fire(engine, 75, 10)) == 1  # gone 25 s (> 20): a new fire


def test_fire_frames_below_three_are_raised_to_three():
    cfg = RuleConfig.from_settings(Settings(_env_file=None, fire_frames=1, smoke_frames=1))
    assert (cfg.fire_frames, cfg.smoke_frames) == (3, 3)
    engine = RulesEngine([], cfg)
    hits = [engine.update(float(i), NOW, W, H, [], fire=[FIRE] if i in (0, 4) else []) for i in range(8)]
    assert all(h == [] for h in hits)  # two separate odd frames never alarm


# ---------- the picture must look like fire / smoke ----------

def picture(bgr) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (60, 60, 60)
    return img if bgr is None else bgr(img)


def flame(img):
    cv2.ellipse(img, (440, 360), (25, 50), 0, 0, 360, (0, 140, 255), -1)  # orange flame (BGR)
    cv2.ellipse(img, (440, 370), (10, 25), 0, 0, 360, (200, 240, 255), -1)  # pale yellow-white core
    return img


def face(img):
    cv2.rectangle(img, (400, 300), (480, 420), (120, 150, 200), -1)  # skin tone in room light
    return img


def lamp(img):
    cv2.rectangle(img, (400, 300), (480, 420), (250, 250, 250), -1)  # bright but colourless
    return img


def orange_shirt(img):
    cv2.rectangle(img, (400, 300), (480, 420), (30, 110, 200), -1)  # orange but not glowing
    return img


def smoke(img):
    cv2.rectangle(img, (400, 300), (480, 420), (170, 172, 168), -1)  # grey haze
    return img


def test_flame_colours_pass_and_look_alikes_fail():
    min_frac = 0.03
    assert flame_fraction(picture(flame), FIRE.box) >= min_frac
    for fake in (face, lamp, orange_shirt, smoke, None):
        assert flame_fraction(picture(fake), FIRE.box) < min_frac, fake


def test_smoke_must_be_grey():
    assert grey_fraction(picture(smoke), FIRE.box) >= 0.35
    assert grey_fraction(picture(face), FIRE.box) < 0.35
    assert grey_fraction(picture(orange_shirt), FIRE.box) < 0.35


def test_verify_drops_fake_fire_and_smoke_on_a_person():
    labels = (("fire", "flame"), ("smoke",))
    smoke_box = DetectionObs(FIRE.box, "smoke", 0.6)
    fire_box = DetectionObs(FIRE.box, "fire", 0.7)
    assert verify_fire_smoke(picture(flame), [fire_box], [], *labels, 0.03, 0.35) == [fire_box]
    assert verify_fire_smoke(picture(face), [fire_box], [], *labels, 0.03, 0.35) == []
    assert verify_fire_smoke(picture(smoke), [smoke_box], [], *labels, 0.03, 0.35) == [smoke_box]
    person = (390, 250, 500, 470)  # the "smoke" sits on a person: it is the person
    assert verify_fire_smoke(picture(smoke), [smoke_box], [person], *labels, 0.03, 0.35) == []


def test_unknown_labels_pass_through():
    helmet = DetectionObs(FIRE.box, "helmet", 0.9)
    assert verify_fire_smoke(picture(None), [helmet], [], ("fire",), ("smoke",), 0.03, 0.35) == [helmet]

