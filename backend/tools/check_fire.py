"""See what the fire/smoke model finds and whether the picture check (P40) keeps or drops each box.

    python -m tools.check_fire --source 0
    python -m tools.check_fire --source http://192.168.137.74:8080/video
    python -m tools.check_fire --source media/training/lighter/lighter_0001.jpg

A window shows the camera: kept boxes are drawn solid (an alarm can come from them), dropped boxes thin with
"DROPPED". Every second it prints each box: label, score, flame / grey share of the box and the verdict.
Use it with a real lighter, then with your face, a lamp and an orange object: the real flame must say KEEP and
the rest DROP. If a real flame says DROP, lower FIRE_COLOUR_MIN in .env (e.g. 0.02); if a fake says KEEP,
raise it. Press Q to stop.
"""

import argparse
import time

import cv2

from app.config import get_settings
from worker.capture import LatestFrameCapture
from worker.detectors import PersonPoseTracker, optional_detector
from worker.rules import DetectionObs, RuleConfig
from worker.verify import flame_fraction, grey_fraction, verify_fire_smoke


def main() -> int:
    parser = argparse.ArgumentParser(description="check fire/smoke detections against the picture check")
    parser.add_argument("--source", default="0", help="0 = laptop webcam, a phone URL, a video or an image file")
    args = parser.parse_args()

    s = get_settings()
    cfg = RuleConfig.from_settings(s)
    detector = optional_detector(s.fire_model_path, s.detect_imgsz, min(s.fire_conf, s.smoke_conf), "fire")
    if detector is None:
        print("FIRE_MODEL_PATH is not set or the file is missing (see .env)")
        return 1
    people = PersonPoseTracker(s.yolo_model_path, s.detect_imgsz, s.detect_conf)
    print(f"fire needs score >= {s.fire_conf} and flame share >= {s.fire_colour_min}; "
          f"smoke needs score >= {s.smoke_conf} and grey share >= {s.smoke_colour_min}")

    image_file = args.source.lower().endswith((".jpg", ".jpeg", ".png"))
    # a camera is read by its own thread that keeps only the newest frame, so the window never lags behind
    capture = None if image_file else LatestFrameCapture(args.source, 1).start()
    last_print, last_id, frames, fps, fps_start = 0.0, None, 0, 0.0, time.monotonic()
    while True:
        if image_file:
            frame = cv2.imread(args.source)
            if frame is None:
                print("could not read the image file")
                return 1
        else:
            latest = capture.latest()
            if latest is None or latest.frame_id == last_id:
                if cv2.waitKey(5) & 0xFF in (ord("q"), ord("Q")):
                    break
                continue
            last_id, frame = latest.frame_id, latest.image.copy()
        found = [DetectionObs(d.xyxy, d.label, d.confidence) for d in detector.detect(frame)]
        found = [d for d in found if d.confidence >= (s.fire_conf if d.label in cfg.fire_labels else s.smoke_conf)]
        # the person model is only needed to drop smoke boxes that sit on a person
        need_people = any(d.label in cfg.smoke_labels for d in found)
        person_boxes = [p.xyxy for p in people.track(frame)] if need_people else []
        kept = verify_fire_smoke(frame, found, person_boxes, cfg.fire_labels, cfg.smoke_labels,
                                 s.fire_colour_min, s.smoke_colour_min)
        frames += 1
        if time.monotonic() - fps_start >= 2.0:
            fps, frames, fps_start = frames / (time.monotonic() - fps_start), 0, time.monotonic()
        view = frame.copy()  # draw on a copy, so the printed shares come from the real picture
        cv2.putText(view, f"{fps:.1f} checks per second", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        now = time.monotonic()
        for d in found:
            ok = d in kept
            x1, y1, x2, y2 = (int(v) for v in d.box)
            colour = (0, 115, 249) if d.label in cfg.fire_labels else (175, 163, 156)
            cv2.rectangle(view, (x1, y1), (x2, y2), colour, 3 if ok else 1)
            cv2.putText(view, f"{d.label} {d.confidence:.2f}{'' if ok else ' DROPPED'}", (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
            if now - last_print >= 1.0:
                print(f"{d.label:6s} score {d.confidence:.2f}  flame {flame_fraction(frame, d.box):.3f}  "
                      f"grey {grey_fraction(frame, d.box):.2f}  -> {'KEEP' if ok else 'DROP'}")
        if found and now - last_print >= 1.0:
            last_print = now
            print("-")
        cv2.imshow("check_fire (Q to stop)", view)
        if cv2.waitKey(0 if image_file else 1) & 0xFF in (ord("q"), ord("Q")) or image_file:
            break
    if capture is not None:
        capture.stop()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
