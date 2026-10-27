"""P40: check that a fire or smoke box really looks like fire or smoke before the rules see it.

The fire/smoke model sometimes scores a face, a lamp, an orange shirt or a grey wall. The rules already need
several sightings, but a thing that is always in the picture is "seen" every frame. So each box is also checked
against the picture itself:

- fire: a flame gives off light. Its middle is blown out to near white (value >= 245, the "hot core") and
  around it the colour glows (value >= 220, saturation >= 60, hue red-to-yellow). The box needs a hot core
  (at least 0.5% of it) AND glowing colour on at least FIRE_COLOUR_MIN of it. A face, skin or an orange shirt
  in room light is coloured but not glowing; a white lamp glows but has no colour.
- smoke: smoke is grey, so most of the box must have little colour (saturation <= 70, not black, not blown
  out), at least SMOKE_COLOUR_MIN of the box. A face or a coloured object fails. A smoke box that sits mostly
  on a person (their head, their shirt) is dropped too: that is the person, not smoke.

Pure functions on a numpy image, so they are tested with synthetic pictures.
"""

import cv2
import numpy as np

from worker.rules import Box, DetectionObs

HOT_CORE_MIN = 0.005  # share of a fire box that must be blown out to near white
ON_PERSON_FRAC = 0.6  # a smoke box with this much of its area inside one person's box is that person


def _crop(image: np.ndarray, box: Box) -> np.ndarray | None:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return image[y1:y2, x1:x2]


def flame_fraction(image: np.ndarray, box: Box) -> float:
    """Share of the box's pixels that glow in flame colours (red, orange, yellow); 0 without a hot core."""
    crop = _crop(image, box)
    if crop is None:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if float((val >= 245).mean()) < HOT_CORE_MIN:
        return 0.0
    glow = (val >= 220) & (sat >= 60) & ((hue <= 35) | (hue >= 170))  # OpenCV hue is 0..179
    return float(glow.mean())


def grey_fraction(image: np.ndarray, box: Box) -> float:
    """Share of the box's pixels that are grey-ish (little colour, not black, not blown out)."""
    crop = _crop(image, box)
    if crop is None:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    grey = (sat <= 70) & (val >= 60) & (val <= 240)
    return float(grey.mean())


def _inside_fraction(inner: Box, outer: Box) -> float:
    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


def verify_fire_smoke(image: np.ndarray, detections: list[DetectionObs], people: list[Box],
                      fire_labels: tuple[str, ...], smoke_labels: tuple[str, ...],
                      fire_min: float, smoke_min: float) -> list[DetectionObs]:
    """Keep only the fire boxes that look like flames and the smoke boxes that look like smoke.
    Other labels pass through unchanged."""
    kept = []
    for d in detections:
        if d.label in fire_labels:
            if flame_fraction(image, d.box) < fire_min:
                continue
        elif d.label in smoke_labels:
            if any(_inside_fraction(d.box, p) >= ON_PERSON_FRAC for p in people):
                continue
            if grey_fraction(image, d.box) < smoke_min:
                continue
        kept.append(d)
    return kept
