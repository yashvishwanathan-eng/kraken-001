"""Rules engine tests: fake detections in, expected incidents out. No camera, model or database."""

from datetime import datetime, timezone

from worker.rules import (
    BagObs, PersonObs, RuleConfig, RulesEngine, ZoneDef, feet_point, is_night, parse_hhmm, point_in_polygon,
)

W, H = 640, 480
DAY = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
NIGHT = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)  # 21:30 IST

RESTRICTED = ZoneDef("server-room-door", "restricted", [(0.6, 0.55), (1.0, 0.55), (1.0, 1.0), (0.6, 1.0)])
WORK = ZoneDef("assembly-line", "work", [(0.0, 0.4), (0.6, 0.4), (0.6, 1.0), (0.0, 1.0)], max_people=2)


def person(track_id, box, angle=5.0, hip_y=None, conf=0.9):
    if hip_y is None:
        hip_y = box[1] + (box[3] - box[1]) * 0.55
    return PersonObs(track_id, box, conf, angle, hip_y)


IN_RESTRICTED = (420, 200, 480, 470)  # feet at x=0.70, y=0.98
NEXT_TO_RESTRICTED = (300, 200, 360, 470)  # feet at x=0.52: inside the work zone, not the restricted one
IN_WORK = (100, 150, 160, 450)


def run(engine, frames, now=DAY, bags_every=None):
    """frames: list of (t, people, bags_or_None). Returns every candidate with the time it fired."""
    fired = []
    for t, people, bags in frames:
        for c in engine.update(t, now, W, H, people, bags):
            fired.append((t, c))
    return fired


def every(step, start, end):
    t = start
    while t <= end + 1e-9:
        yield round(t, 3)
        t += step


def engine(*zones, **cfg):
    return RulesEngine(list(zones), RuleConfig(**cfg))


# ---------- helpers ----------

def test_feet_point_is_bottom_centre_as_fraction():
    assert feet_point((100, 100, 200, 480), W, H) == (150 / W, 1.0)


def test_point_in_polygon():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon((0.5, 0.5), square)
    assert not point_in_polygon((1.5, 0.5), square)


def test_night_window_crosses_midnight():
    start, end = parse_hhmm("20:00"), parse_hhmm("06:00")
    assert not is_night(DAY, start, end, "Asia/Kolkata")
    assert is_night(NIGHT, start, end, "Asia/Kolkata")
    assert is_night(datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc), start, end, "Asia/Kolkata")  # 04:30 IST


# ---------- access ----------

def test_access_fires_after_one_and_a_half_seconds_in_restricted_zone():
    fired = run(engine(RESTRICTED), [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 5)])
    assert len(fired) == 1
    t, c = fired[0]
    assert c.type == "access" and c.severity == "high" and c.zone == "server-room-door"
    assert 1.5 <= t < 1.9  # ACCESS_DWELL_S (P41: 1.5 s)


def test_access_does_not_fire_for_short_visit():
    frames = [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 1.2)]
    frames += [(t, [], None) for t in every(0.3, 1.5, 6)]
    assert run(engine(RESTRICTED), frames) == []


def test_standing_next_to_zone_does_not_trigger_access():
    assert run(engine(RESTRICTED), [(t, [person(1, NEXT_TO_RESTRICTED)], None) for t in every(0.3, 0, 10)]) == []


def test_access_at_night_is_critical():
    fired = run(engine(RESTRICTED), [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 5)], now=NIGHT)
    assert [c.severity for _, c in fired] == ["critical"]


def test_brief_track_loss_within_grace_keeps_the_timer():
    frames = [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 1.5)]
    frames += [(2.1, [], None)]  # one missed frame (0.6 s gap, under the 1 s grace)
    frames += [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 2.4, 4)]
    fired = run(engine(RESTRICTED), frames)
    assert len(fired) == 1 and fired[0][0] < 3.4


# ---------- cooldown ----------

def test_cooldown_stops_repeat_incidents():
    """P40: someone who stays in the zone is one alarm, however long they stay."""
    fired = run(engine(RESTRICTED), [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.5, 0, 200)])
    assert len([c for _, c in fired if c.type == "access"]) == 1


def test_a_new_entry_after_leaving_alarms_again():
    """Left for longer than REARM_QUIET_S (20 s) and the cooldown (60 s) passed: a new entry is a new alarm."""
    frames = ([(t, [person(1, IN_RESTRICTED)], None) for t in every(0.5, 0, 10)]
              + [(t, [], None) for t in every(0.5, 10, 70)]
              + [(t, [person(2, IN_RESTRICTED)], None) for t in every(0.5, 70, 80)])
    times = [t for t, _ in run(engine(RESTRICTED), frames)]
    assert len(times) == 2 and times[1] - times[0] >= 60


# ---------- loitering ----------

def test_loitering_fires_after_120_seconds():
    fired = run(engine(WORK), [(t, [person(3, IN_WORK)], None) for t in every(1.0, 0, 125)])
    loiter = [(t, c) for t, c in fired if c.type == "loitering"]
    assert len(loiter) == 1 and loiter[0][0] == 120 and loiter[0][1].severity == "low"


# ---------- crowding ----------

def test_crowding_fires_when_more_than_n_people():
    two = [person(1, IN_WORK), person(2, (200, 150, 260, 450))]
    three = two + [person(4, (40, 150, 100, 450))]
    assert run(engine(WORK), [(0.0, two, None)]) == []
    fired = run(engine(WORK), [(0.0, three, None)])
    assert [(c.type, c.severity, len(c.track_ids)) for _, c in fired] == [("crowding", "high", 3)]


# ---------- fall ----------

STANDING = (300, 100, 360, 460)  # tall box, hips around y=300
LYING = (220, 380, 460, 460)  # wide box near the floor


def test_fall_fires_after_five_seconds_down():
    frames = [(t, [person(5, STANDING, angle=5, hip_y=300)], None) for t in every(0.5, 0, 2)]
    frames += [(t, [person(5, LYING, angle=85, hip_y=420)], None) for t in every(0.5, 2.5, 9)]
    fired = run(engine(), frames)
    assert [(c.type, c.severity) for _, c in fired] == [("fall", "critical")]
    assert 7.5 <= fired[0][0] < 8.0


def test_bending_is_not_a_fall():
    frames = [(t, [person(5, STANDING, angle=5, hip_y=300)], None) for t in every(0.5, 0, 2)]
    # deep bend: torso tilts past 60 degrees but hips stay at about the same height
    frames += [(t, [person(5, (280, 220, 380, 460), angle=70, hip_y=310)], None) for t in every(0.5, 2.5, 12)]
    assert run(engine(), frames) == []


def test_getting_up_before_five_seconds_is_not_a_fall():
    frames = [(t, [person(5, STANDING, angle=5, hip_y=300)], None) for t in every(0.5, 0, 2)]
    frames += [(t, [person(5, LYING, angle=85, hip_y=420)], None) for t in every(0.5, 2.5, 5)]
    frames += [(t, [person(5, STANDING, angle=5, hip_y=300)], None) for t in every(0.5, 5.5, 12)]
    assert run(engine(), frames) == []


# ---------- unattended bag ----------

BAG = BagObs((500, 400, 540, 450), "backpack", 0.8)


def test_bag_left_alone_for_60_seconds_is_unattended():
    frames = [(t, [], [BAG]) for t in every(1.0, 0, 65)]
    fired = run(engine(RESTRICTED), frames)
    assert [(t, c.type, c.severity, c.zone) for t, c in fired] == [(60.0, "unattended", "high", "server-room-door")]


def test_bag_next_to_its_owner_is_not_unattended():
    owner = person(9, (450, 200, 510, 460))
    assert run(engine(), [(t, [owner], [BAG]) for t in every(1.0, 0, 90)]) == []


def test_owner_returning_resets_the_unattended_timer():
    owner = person(9, (450, 200, 510, 460))
    frames = [(t, [], [BAG]) for t in every(1.0, 0, 40)]
    frames += [(41.0, [owner], [BAG])]
    frames += [(t, [], [BAG]) for t in every(1.0, 42, 110)]
    fired = run(engine(), frames)
    assert [t for t, _ in fired] == [102.0]  # alone again from t=42, so 60 s later


def test_fire_and_smoke_are_separate_and_a_small_flame_counts():
    from worker.rules import DetectionObs, RuleConfig, RulesEngine

    engine = RulesEngine([], RuleConfig(fire_frames=3, smoke_frames=3, cooldown_s=60))
    lighter = [DetectionObs((300, 200, 320, 240), "fire", 0.4)]  # small flame, 0.4: above FIRE_CONF (0.35)
    found = []
    for i in range(3):
        found += engine.update(float(i), datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc), 640, 480, [],
                               fire=lighter)
    assert [(c.type, c.severity) for c in found] == [("fire", "critical")]
    smoke = [DetectionObs((100, 50, 400, 300), "smoke", 0.7)]
    found = []
    for i in range(3):
        found += engine.update(10.0 + i, datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc), 640, 480, [],
                               fire=smoke)
    assert [(c.type, c.severity) for c in found] == [("smoke", "high")]


def test_rider_without_a_helmet():
    from worker.rules import DetectionObs, RuleConfig, RulesEngine

    engine = RulesEngine([], RuleConfig(no_helmet_frames=2))
    rider = [DetectionObs((300, 100, 360, 160), "Without Helmet".lower(), 0.8)]
    with_helmet = [DetectionObs((300, 100, 360, 160), "with helmet", 0.9)]
    now = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    assert engine.update(0.0, now, 640, 480, [], riders=with_helmet) == []
    assert engine.update(1.0, now, 640, 480, [], riders=rider) == []
    found = engine.update(2.0, now, 640, 480, [], riders=rider)
    assert [(c.type, c.severity) for c in found] == [("no_helmet", "low")]


def test_a_flickering_flame_still_counts_but_one_stray_frame_never_does():
    from worker.rules import DetectionObs, RuleConfig, RulesEngine

    engine = RulesEngine([], RuleConfig(fire_frames=5, cooldown_s=60))
    flame = [DetectionObs((300, 200, 320, 240), "fire", 0.5)]
    day = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    pattern = [flame, flame, [], flame, flame, [], flame]  # the model misses the flame now and then
    found = [c for i, f in enumerate(pattern) for c in engine.update(float(i), day, 640, 480, [], fire=f)]
    assert [c.type for c in found] == ["fire"] and "5 of the last" in found[0].reason

    engine = RulesEngine([], RuleConfig(fire_frames=5, cooldown_s=60))
    stray = [flame, [], [], flame, [], [], flame, [], [], flame]  # an orange shirt now and then
    assert [c for i, f in enumerate(stray) for c in engine.update(float(i), day, 640, 480, [], fire=f)] == []
