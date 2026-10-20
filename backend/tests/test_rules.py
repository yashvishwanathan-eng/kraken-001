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

def test_access_fires_after_three_seconds_in_restricted_zone():
    fired = run(engine(RESTRICTED), [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 5)])
    assert len(fired) == 1
    t, c = fired[0]
    assert c.type == "access" and c.severity == "high" and c.zone == "server-room-door"
    assert 3.0 <= t < 3.4


def test_access_does_not_fire_for_short_visit():
    frames = [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.3, 0, 2.4)]
    frames += [(t, [], None) for t in every(0.3, 2.7, 6)]
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
    fired = run(engine(RESTRICTED), [(t, [person(1, IN_RESTRICTED)], None) for t in every(0.5, 0, 70)])
    times = [t for t, _ in fired]
    assert len(times) == 2
    assert times[1] - times[0] >= 60


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
