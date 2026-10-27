"""Incident rules: pure functions plus small per-track timers.

Nothing here touches a camera, a model or the database. The worker turns model output into
PersonObs / BagObs, calls RulesEngine.update() once per analysed frame, and gets back the
incidents that just became true. That keeps every rule testable with fake detections.

Zone polygons are fractions of the frame (0..1), so the same zone works at any resolution.
"""

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels

BAG_IOU_MATCH = 0.3  # a new bag box overlapping an old one this much is the same bag
BAG_FORGET_S = 5.0  # forget a bag not seen for this long
UPRIGHT_FORGET_S = 30.0  # forget a person's upright hip height after this long unseen
STREAK_SLACK = 3  # fire, smoke, no helmet: N sightings within the last N + 3 analysed frames (5 of 8)
MIN_CONFIRM_FRAMES = 3  # P40: fire and smoke always need at least 3 sightings; one or two odd frames never alarm


# ---------- data in and out ----------

@dataclass(frozen=True)
class ZoneDef:
    name: str
    kind: str  # restricted, ppe, work
    polygon: list[Point]
    max_people: int | None = None
    allowed: frozenset[str] = frozenset()  # restricted zones: names whose recognised face may enter (P34)


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
class DetectionObs:
    """A box from an extra model (PPE or fire/smoke), with its class name lower-cased."""
    box: Box
    label: str
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
    rearm_quiet_s: float = 20.0  # after an alarm, that type re-arms only once it has not been seen for this long
    access_dwell_s: float = 1.5
    loiter_s: float = 120.0
    fall_angle_deg: float = 60.0
    upright_angle_deg: float = 30.0
    fall_hip_drop: float = 0.25
    fall_down_s: float = 5.0
    unattended_s: float = 60.0
    bag_still_frac: float = 0.03
    bag_reach_frac: float = 0.15
    track_grace_s: float = 1.0
    ppe_missing_frames: int = 10
    fire_frames: int = 3
    smoke_frames: int = 3
    no_helmet_frames: int = 4
    extra_model_conf: float = 0.5
    fire_conf: float = 0.35  # a lighter's small flame scores lower than a big fire
    smoke_conf: float = 0.5
    helmet_labels: tuple[str, ...] = ("helmet", "hardhat", "hard-hat")
    vest_labels: tuple[str, ...] = ("vest", "safety-vest", "safety vest")
    fire_labels: tuple[str, ...] = ("fire", "flame")
    smoke_labels: tuple[str, ...] = ("smoke",)
    no_helmet_labels: tuple[str, ...] = ("without helmet", "without_helmet", "no helmet", "no-helmet", "no_helmet",
                                         "nohelmet", "without-helmet")
    night_start: time = time(20, 0)
    night_end: time = time(6, 0)
    timezone: str = "Asia/Kolkata"

    @classmethod
    def from_settings(cls, s) -> "RuleConfig":
        return cls(
            cooldown_s=s.incident_cooldown_s, rearm_quiet_s=s.rearm_quiet_s, access_dwell_s=s.access_dwell_s, loiter_s=s.loiter_s,
            fall_angle_deg=s.fall_angle_deg, upright_angle_deg=s.upright_angle_deg,
            fall_hip_drop=s.fall_hip_drop, fall_down_s=s.fall_down_s, unattended_s=s.unattended_s,
            bag_still_frac=s.bag_still_frac, bag_reach_frac=s.bag_reach_frac,
            track_grace_s=s.track_grace_s, ppe_missing_frames=s.ppe_missing_frames,
            fire_frames=max(s.fire_frames, MIN_CONFIRM_FRAMES), smoke_frames=max(s.smoke_frames, MIN_CONFIRM_FRAMES), no_helmet_frames=s.no_helmet_frames, fire_conf=s.fire_conf, smoke_conf=s.smoke_conf,
            extra_model_conf=s.detect_conf, helmet_labels=labels(s.ppe_helmet_labels),
            vest_labels=labels(s.ppe_vest_labels), fire_labels=labels(s.fire_labels),
            smoke_labels=labels(s.smoke_labels), no_helmet_labels=labels(s.no_helmet_labels),
            night_start=parse_hhmm(s.night_start),
            night_end=parse_hhmm(s.night_end), timezone=s.timezone,
        )


# ---------- pure geometry and time helpers ----------

def labels(csv: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in csv.split(",") if part.strip())


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
        self._last_true: dict[str, float] = {}  # type -> last analysed time its condition was true (fired or not)
        self._ppe_missing: dict[int, int] = {}  # track -> consecutive analysed frames without helmet/vest
        # the last few analysed frames for each kind: True = seen (fire flickers, so a miss does not reset the count)
        self._streaks: dict[str, deque[bool]] = {"fire": deque(), "smoke": deque(), "no_helmet": deque()}

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
        ppe: list[DetectionObs] | None = None,
        fire: list[DetectionObs] | None = None,
        riders: list[DetectionObs] | None = None,
        identities: dict[int, str] | None = None,
    ) -> list[Candidate]:
        """Feed one analysed frame. `t` is a monotonic clock in seconds.

        `identities` (face recognition): track id -> the recognised person's name, or "" when a face was seen
        but matched nobody; a track missing from it has shown no usable face yet.
        `bags`, `ppe`, `fire` and `riders` (helmet model) are None when that model did not run on this frame (or is not installed),
        which leaves its counters untouched.
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
                    who = (identities or {}).get(p.track_id)
                    if who and who in zone.allowed:
                        continue  # a recognised person who may be here: no alert
                    dwell = self._access.tick((p.track_id, zone.name), t)
                    if dwell >= cfg.access_dwell_s:
                        whom = ("unknown face" if who == "" else "face not visible" if who is None
                                else f"{who}, not allowed here")
                        found.append(Candidate("access", "critical" if night else "high", zone.name, p.confidence,
                                               (p.track_id,), f"inside {zone.name} for {dwell:.1f}s ({whom})"))
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
        if ppe is not None:
            found.extend(self._update_ppe(people, zones_of, ppe))
        if fire is not None:
            found.extend(self._update_fire(frame_w, frame_h, fire))
        if riders is not None:
            found.extend(self._update_no_helmet(frame_w, frame_h, riders))

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

    def _update_ppe(self, people: list[PersonObs], zones_of: dict[int, list[ZoneDef]],
                    detections: list[DetectionObs]) -> list[Candidate]:
        """Person in a ppe zone without a helmet (top of their box) or a vest (middle of their box)
        in N consecutive analysed frames."""
        cfg = self.cfg
        helmets = [d for d in detections if d.label in cfg.helmet_labels and d.confidence >= cfg.extra_model_conf]
        vests = [d for d in detections if d.label in cfg.vest_labels and d.confidence >= cfg.extra_model_conf]
        found, seen = [], set()
        for p in people:
            ppe_zone = next((z for z in zones_of[p.track_id] if z.kind == "ppe"), None)
            if ppe_zone is None:
                continue
            seen.add(p.track_id)
            x1, y1, x2, y2 = p.box
            height = y2 - y1

            def centre_in(det: DetectionObs, top: float, bottom: float) -> bool:
                cx, cy = box_center(det.box)
                return x1 <= cx <= x2 and y1 + top * height <= cy <= y1 + bottom * height

            missing = []
            if not any(centre_in(h, -0.1, 0.4) for h in helmets):  # helmets can stick out above the box
                missing.append("helmet")
            if not any(centre_in(v, 0.2, 0.8) for v in vests):
                missing.append("vest")
            if not missing:
                self._ppe_missing.pop(p.track_id, None)
                continue
            count = self._ppe_missing.get(p.track_id, 0) + 1
            self._ppe_missing[p.track_id] = count
            if count >= cfg.ppe_missing_frames:
                found.append(Candidate("ppe", "low", ppe_zone.name, p.confidence, (p.track_id,),
                                       f"no {' or '.join(missing)} for {count} frames"))
        self._ppe_missing = {k: v for k, v in self._ppe_missing.items() if k in seen}
        return found

    def _streak(self, kind: str, severity: str, needed: int, conf: float, labels_: tuple[str, ...],
                frame_w: int, frame_h: int, detections: list[DetectionObs]) -> list[Candidate]:
        """One kind of thing seen in N of the last N + STREAK_SLACK analysed frames (confidence at least `conf`)
        -> a candidate. A flame flickers and the model misses it now and then; one miss must not start the count
        again, but one stray frame can never raise an alarm either."""
        seen = [d for d in detections if d.label in labels_ and d.confidence >= conf]
        history = self._streaks[kind]
        history.append(bool(seen))
        while len(history) > needed + STREAK_SLACK:
            history.popleft()
        hits = sum(history)
        if not seen or hits < needed:
            return []
        strongest = max(seen, key=lambda d: d.confidence)
        spot = feet_point(strongest.box, frame_w, frame_h)
        zone = next((z.name for z in self.zones if point_in_polygon(spot, z.polygon)), None)
        return [Candidate(kind, severity, zone, strongest.confidence, (),
                          f"{strongest.label} in {hits} of the last {len(history)} frames")]

    def _update_fire(self, frame_w: int, frame_h: int, detections: list[DetectionObs]) -> list[Candidate]:
        """Fire (critical) and smoke (high) are separate incidents, each needing N frames in a row. Flames use a
        lower confidence (FIRE_CONF) because a small flame, like a lighter, scores lower than a big fire."""
        cfg = self.cfg
        return (self._streak("fire", "critical", cfg.fire_frames, cfg.fire_conf, cfg.fire_labels,
                             frame_w, frame_h, detections)
                + self._streak("smoke", "high", cfg.smoke_frames, cfg.smoke_conf, cfg.smoke_labels,
                               frame_w, frame_h, detections))

    def _update_no_helmet(self, frame_w: int, frame_h: int, detections: list[DetectionObs]) -> list[Candidate]:
        """A rider without a helmet (helmet model) in N frames in a row; the gate guard of this camera is called."""
        cfg = self.cfg
        return self._streak("no_helmet", "low", cfg.no_helmet_frames, cfg.extra_model_conf, cfg.no_helmet_labels,
                            frame_w, frame_h, detections)

    def _apply_cooldown(self, t: float, found: list[Candidate]) -> list[Candidate]:
        """One alarm per event. A type that fired stays quiet while its condition keeps being true (the same fire,
        the same person still in the zone); it re-arms only after the condition has been gone for REARM_QUIET_S
        and the cooldown has passed. One worker = one camera, so the key is the type."""
        fired = []
        for c in found:
            if any(f.type == c.type for f in fired):
                continue
            last_true = self._last_true.get(c.type)
            self._last_true[c.type] = t
            last = self._last_fired.get(c.type)
            if last is not None:
                if t - last < self.cfg.cooldown_s:
                    continue
                if last_true is not None and t - last_true < self.cfg.rearm_quiet_s:
                    continue  # still the same event: it never went away
            self._last_fired[c.type] = t
            fired.append(c)
        return fired
