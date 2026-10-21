"""PPE and fire/smoke rules with fake detections (the models themselves are not chosen yet)."""

from datetime import datetime, timezone

from worker.rules import DetectionObs, PersonObs, RuleConfig, RulesEngine, ZoneDef

W, H = 640, 480
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
PPE_ZONE = ZoneDef("helmet-area", "ppe", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
WORKER = PersonObs(1, (100, 100, 200, 460), 0.9, 5.0, 300.0)  # box height 360
HELMET = DetectionObs((130, 85, 170, 125), "helmet", 0.8)  # centre y=105: top of the box
VEST = DetectionObs((110, 200, 190, 300), "vest", 0.8)  # centre y=250: middle of the box
FIRE = DetectionObs((400, 300, 480, 420), "fire", 0.7)


def feed(engine, frames, **kwargs):
    fired = []
    for i, extra in enumerate(frames):
        fired += [(i, c) for c in engine.update(float(i), NOW, W, H, [WORKER], None, **extra, **kwargs)]
    return fired


def test_no_helmet_for_ten_frames_is_a_ppe_incident():
    engine = RulesEngine([PPE_ZONE], RuleConfig())
    fired = feed(engine, [{"ppe": [VEST]}] * 12)
    assert [(i, c.type, c.severity, c.zone) for i, c in fired] == [(9, "ppe", "low", "helmet-area")]
    assert "helmet" in fired[0][1].reason


def test_helmet_and_vest_means_no_incident():
    engine = RulesEngine([PPE_ZONE], RuleConfig())
    assert feed(engine, [{"ppe": [HELMET, VEST]}] * 20) == []


def test_putting_the_helmet_on_resets_the_count():
    engine = RulesEngine([PPE_ZONE], RuleConfig())
    frames = [{"ppe": [VEST]}] * 8 + [{"ppe": [HELMET, VEST]}] + [{"ppe": [VEST]}] * 8
    assert feed(engine, frames) == []


def test_ppe_model_not_run_leaves_the_count_alone():
    engine = RulesEngine([PPE_ZONE], RuleConfig())
    frames = [{"ppe": [VEST]}] * 5 + [{"ppe": None}] * 5 + [{"ppe": [VEST]}] * 5
    fired = feed(engine, frames)
    assert [i for i, _ in fired] == [14]  # 10th frame where the model actually looked


def test_fire_in_five_frames_in_a_row():
    engine = RulesEngine([], RuleConfig())
    fired = feed(engine, [{"fire": [FIRE]}] * 6)
    assert [(i, c.type, c.severity) for i, c in fired] == [(4, "fire", "critical")]


def test_flicker_does_not_count_as_fire():
    engine = RulesEngine([], RuleConfig())
    frames = ([{"fire": [FIRE]}] * 4 + [{"fire": []}]) * 3
    assert feed(engine, frames) == []


def test_weak_fire_detection_is_ignored():
    engine = RulesEngine([], RuleConfig())
    weak = DetectionObs(FIRE.box, "smoke", 0.3)
    assert feed(engine, [{"fire": [weak]}] * 10) == []
