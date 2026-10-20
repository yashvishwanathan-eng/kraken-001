"""Incident rules: pure functions plus small per-track timers.

Nothing here touches a camera, a model or the database. The worker turns model output into
PersonObs / BagObs, calls RulesEngine.update() once per analysed frame, and gets back the
incidents that just became true. That keeps every rule testable with fake detections.

Zone polygons are fractions of the frame (0..1), so the same zone works at any resolution.
"""

import math
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels

BAG_IOU_MATCH = 0.3  # a new bag box overlapping an old one this much is the same bag
BAG_FORGET_S = 5.0  # forget a bag not seen for this long
UPRIGHT_FORGET_S = 30.0  # forget a person's upright hip height after this long unseen


# ---------- data in and out ----------

@dataclass(frozen=True)
class ZoneDef:
    name: str
    kind: str  # restricted, ppe, work
    polygon: list[Point]
    max_people: int | None = None


@dataclass(frozen=True)
class PersonObs:
    track_id: int
    box: Box
    confidence: float
    torso_angle: float | None  # degrees from vertical, None if shoulders/hips not visible
    hip_y: float | None  # hip midpoint height in pixels


@dataclass(frozen=True)
class BagObs:
    box: Box
    label: str  # backpack, handbag, suitcase
    confidence: float


@dataclass(frozen=True)
class Candidate:
    type: str
    severity: str
    zone: str | None
    confidence: float
    track_ids: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class RuleConfig:
    cooldown_s: float = 60.0
    access_dwell_s: float = 3.0
    loiter_s: float = 120.0
    fall_angle_deg: float = 60.0
    upright_angle_deg: float = 30.0
    fall_hip_drop: float = 0.25
    fall_down_s: float = 5.0
    unattended_s: float = 60.0
    bag_still_frac: float = 0.03
    bag_reach_frac: float = 0.15
    track_grace_s: float = 1.0
    night_start: time = time(20, 0)
    night_end: time = time(6, 0)
    timezone: str = "Asia/Kolkata"

    @classmethod
    def from_settings(cls, s) -> "RuleConfig":
        return cls(
            cooldown_s=s.incident_cooldown_s, access_dwell_s=s.access_dwell_s, loiter_s=s.loiter_s,
            fall_angle_deg=s.fall_angle_deg, upright_angle_deg=s.upright_angle_deg,
            fall_hip_drop=s.fall_hip_drop, fall_down_s=s.fall_down_s, unattended_s=s.unattended_s,
            bag_still_frac=s.bag_still_frac, bag_reach_frac=s.bag_reach_frac,
            track_grace_s=s.track_grace_s, night_start=parse_hhmm(s.night_start),
            night_end=parse_hhmm(s.night_end), timezone=s.timezone,
        )


# ---------- pure geometry and time helpers ----------

def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def feet_point(box: Box, frame_w: int, frame_h: int) -> Point:
    """Bottom-centre of a box, as fractions of the frame. Where a person actually stands."""
    x1, _, x2, y2 = box
    return ((x1 + x2) / 2 / frame_w, y2 / frame_h)


def point_in_polygon(point: Point, polygon: list[Point]) -> bool:
    """Ray casting: count how many polygon edges a ray to the right crosses. Odd = inside."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def is_night(now_utc: datetime, start: time, end: time, tz: str) -> bool:
    """True inside the night window. Handles windows that cross midnight (20:00-06:00)."""
    local = now_utc.astimezone(ZoneInfo(tz)).time()
    if start > end:
        return local >= start or local < end
    return start <= local < end


def box_center(box: Box) -> Point:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def iou(a: Box, b: Box) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def distance_to_box(point: Point, box: Box) -> float:
    """Shortest distance from a point to a rectangle (0 if the point is inside it)."""
    x, y = point
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return math.hypot(dx, dy)


# ---------- small state ----------

class DwellTimers:
    """Remembers when each key (a track, or a track+zone pair) started a condition."""

    def __init__(self) -> None:
        self._start: dict[object, float] = {}
        self._seen: dict[object, float] = {}

    def tick(self, key: object, t: float) -> float:
        """Condition is true now: return how long it has been true."""
        start = self._start.setdefault(key, t)
        self._seen[key] = t
        return t - start

    def reset(self, key: object) -> None:
        self._start.pop(key, None)
        self._seen.pop(key, None)

    def prune(self, t: float, grace_s: float) -> None:
        """Drop timers whose condition was not seen for longer than grace_s."""
        for key in [k for k, seen in self._seen.items() if t - seen > grace_s]:
            self.reset(key)


@dataclass
class _BagState:
    box: Box
    label: str
    confidence: float
    anchor: Point  # centre when it last stopped moving
    still_since: float
    unattended_since: float | None
    last_seen: float


# ---------- the engine ----------

class RulesEngine:
    def __init__(self, zones: list[ZoneDef], cfg: RuleConfig) -> None:
        self.zones = zones
        self.cfg = cfg
        self._access = DwellTimers()
        self._loiter = DwellTimers()
        self._down = DwellTimers()
        self._upright: dict[int, tuple[float, float, float]] = {}  # track -> (hip_y, height, last_seen)
        self._bags: list[_BagState] = []
        self._last_fired: dict[str, float] = {}

    def set_zones(self, zones: list[ZoneDef]) -> None:
        self.zones = zones

    def update(
        self,
        t: float,
        now_utc: datetime,
        frame_w: int,
        frame_h: int,
        people: list[PersonObs],
        bags: list[BagObs] | None = None,
    ) -> list[Candidate]:
        """Feed one analysed frame. `t` is a monotonic clock in seconds.

        `bags` is None when bags were not checked on this frame (they are checked about once a second).
        """
        cfg = self.cfg
        found: list[Candidate] = []
        night = is_night(now_utc, cfg.night_start, cfg.night_end, cfg.timezone)
        zones_of = {
            p.track_id: [z for z in self.zones if point_in_polygon(feet_point(p.box, frame_w, frame_h), z.polygon)]
            for p in people
        }

        for p in people:
            for zone in zones_of[p.track_id]:
                if zone.kind == "restricted":
                    dwell = self._access.tick((p.track_id, zone.name), t)
                    if dwell >= cfg.access_dwell_s:
                        found.append(Candidate("access", "critical" if night else "high", zone.name, p.confidence,
                                               (p.track_id,), f"inside {zone.name} for {dwell:.1f}s"))
                dwell = self._loiter.tick((p.track_id, zone.name), t)
                if dwell >= cfg.loiter_s:
                    found.append(Candidate("loitering", "low", zone.name, p.confidence, (p.track_id,),
                                           f"in {zone.name} for {dwell:.0f}s"))

        for zone in self.zones:
            if zone.max_people is None:
                continue
            inside = [p for p in people if zone in zones_of[p.track_id]]
            if len(inside) > zone.max_people:
                found.append(Candidate("crowding", "high", zone.name,
                                       sum(p.confidence for p in inside) / len(inside),
                                       tuple(p.track_id for p in inside),
                                       f"{len(inside)} people, limit {zone.max_people}"))

        for p in people:
            if self._is_down(p, t):
                dwell = self._down.tick(p.track_id, t)
                if dwell >= cfg.fall_down_s:
                    zone = zones_of[p.track_id][0].name if zones_of[p.track_id] else None
                    found.append(Candidate("fall", "critical", zone, p.confidence, (p.track_id,),
                                           f"down for {dwell:.1f}s"))
            else:
                self._down.reset(p.track_id)

        if bags is not None:
            found.extend(self._update_bags(t, frame_w, frame_h, bags, people))

        for timers in (self._access, self._loiter, self._down):
            timers.prune(t, cfg.track_grace_s)
        self._upright = {k: v for k, v in self._upright.items() if t - v[2] <= UPRIGHT_FORGET_S}
        return self._apply_cooldown(t, found)

    def _is_down(self, p: PersonObs, t: float) -> bool:
        """Torso near-horizontal AND hips well below where they were when upright. Bending keeps hips up."""
        cfg = self.cfg
        height = p.box[3] - p.box[1]
        if p.torso_angle is not None and p.torso_angle < cfg.upright_angle_deg and p.hip_y is not None:
            self._upright[p.track_id] = (p.hip_y, height, t)
            return False
        if p.torso_angle is None or p.torso_angle < cfg.fall_angle_deg:
            return False
        upright = self._upright.get(p.track_id)
        if upright is not None and p.hip_y is not None:
            upright_hip_y, upright_height, _ = upright
            return p.hip_y - upright_hip_y >= cfg.fall_hip_drop * upright_height
        # Never seen this person upright (e.g. first seen on the floor): a box wider than tall means lying.
        return (p.box[2] - p.box[0]) >= height

    def _update_bags(self, t: float, frame_w: int, frame_h: int, bags: list[BagObs],
                     people: list[PersonObs]) -> list[Candidate]:
        cfg = self.cfg
        matched: list[_BagState] = []
        for obs in bags:
            best = max((s for s in self._bags if s not in matched), key=lambda s: iou(s.box, obs.box), default=None)
            center = box_center(obs.box)
            if best is not None and iou(best.box, obs.box) >= BAG_IOU_MATCH:
                if math.dist(center, best.anchor) > cfg.bag_still_frac * frame_w:
                    best.anchor, best.still_since = center, t  # it moved: restart the "still" clock
                best.box, best.confidence, best.last_seen = obs.box, obs.confidence, t
                matched.append(best)
            else:
                state = _BagState(obs.box, obs.label, obs.confidence, center, t, None, t)
                self._bags.append(state)
                matched.append(state)
        self._bags = [s for s in self._bags if t - s.last_seen <= BAG_FORGET_S]

        found = []
        reach = cfg.bag_reach_frac * frame_w
        for bag in matched:
            center = box_center(bag.box)
            attended = any(distance_to_box(center, p.box) <= reach for p in people)
            if attended:
                bag.unattended_since = None
            elif bag.unattended_since is None:
                bag.unattended_since = t
            if bag.unattended_since is None:
                continue
            alone_for = min(t - bag.still_since, t - bag.unattended_since)
            if alone_for >= cfg.unattended_s:
                spot = feet_point(bag.box, frame_w, frame_h)
                zone = next((z.name for z in self.zones if point_in_polygon(spot, z.polygon)), None)
                found.append(Candidate("unattended", "high", zone, bag.confidence, (),
                                       f"{bag.label} still and alone for {alone_for:.0f}s"))
        return found

    def _apply_cooldown(self, t: float, found: list[Candidate]) -> list[Candidate]:
        """Same camera + type cannot fire again within the cooldown. One worker = one camera."""
        fired = []
        for c in found:
            last = self._last_fired.get(c.type)
            if last is not None and t - last < self.cfg.cooldown_s:
                continue
            self._last_fired[c.type] = t
            fired.append(c)
        return fired
