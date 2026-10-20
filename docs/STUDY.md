# Study notes: SafetyAI backend

Written as we build. Each topic has: what it is in plain words, a tiny example with the real
output we got, where it lives in our code, and a question a judge might ask.
The tiny examples were run outside the repo; the code is copied here so you can run it yourself:
save it as a `.py` file and run `python file.py` inside the backend venv.

Contents
- Part 1 (19 Oct): video and detection — frames and FPS, the latest-frame thread, YOLO results, tracking IDs, pose and torso angle, MJPEG, tokens
- Part 2 (20 Oct, Gate 1): rules and incidents — zones and the feet point, rules as timers, cooldown, night window, the five rules, ring buffer and clips, the incident flow, tests
- Part 3 (21 Oct, Gate 2): login, roles and the API — added when built
- Commands for your Windows laptop

---

## Part 1 (19 Oct): video and detection

### 1.1 Frames and FPS
**Plain words.** A camera gives you a stream of still pictures (frames). FPS = frames per second.
A webcam usually gives 30. In OpenCV each frame is a NumPy array of shape `(height, width, 3)`,
colours in **BGR** order (blue, green, red), not RGB.

There are two different FPS numbers in our worker:
- **camera FPS**: how many frames the camera delivers per second.
- **analysed FPS**: how many frames the AI actually checks per second. We analyse every 3rd frame,
  so a 15 fps file gives 5 analysed fps (that is exactly what the Gate 1 run printed).

**Where.** `worker/capture.py` (camera FPS), `worker/run_worker.py` (analysed FPS, printed every 5 s).
**Judge asks:** *"Why not analyse every frame?"* People do not move far in 0.1 s; analysing every 3rd
frame keeps the laptop fast enough for two cameras. Our target is 8+ analysed FPS per camera.

### 1.2 The latest-frame capture thread
**Plain words.** Reading the camera and running the AI in the same loop is a trap: if the AI is
slower than the camera, frames pile up in a queue and the AI works on older and older pictures.
So one **thread** (a second line of work inside the same program) does nothing but read the camera
and overwrite one "latest" slot. The AI always takes whatever is newest.

**Tiny example** (camera every 33 ms, AI takes 150 ms per frame):
```python
import queue, threading, time
CAM_INTERVAL, DETECT_TIME, RUN_FOR = 0.033, 0.150, 3.0

def camera(put, stop):
    frame_id = 0
    while not stop.is_set():
        frame_id += 1; put(frame_id, time.monotonic()); time.sleep(CAM_INTERVAL)

def run_sequential():   # A: oldest-first queue
    q = queue.Queue(); stop = threading.Event()
    threading.Thread(target=camera, args=(lambda i, t: q.put((i, t)), stop), daemon=True).start()
    ages = []; end = time.monotonic() + RUN_FOR
    while time.monotonic() < end:
        _, made_at = q.get(); time.sleep(DETECT_TIME); ages.append(time.monotonic() - made_at)
    stop.set(); return ages

def run_latest():       # B: keep only the newest frame
    latest = {"t": 0.0}; lock = threading.Lock(); stop = threading.Event()
    def put(i, t):
        with lock: latest["t"] = t
    threading.Thread(target=camera, args=(put, stop), daemon=True).start()
    ages = []; end = time.monotonic() + RUN_FOR
    while time.monotonic() < end:
        with lock: t = latest["t"]
        time.sleep(DETECT_TIME); ages.append(time.monotonic() - t)
    stop.set(); return ages

for name, fn in [("A: oldest-first queue", run_sequential), ("B: latest-frame thread", run_latest)]:
    ages = fn(); print(name, f"first={ages[0]*1000:.0f} ms, last={ages[-1]*1000:.0f} ms")
```
Output:
```
A: oldest-first queue: first=151 ms, last=2375 ms      <- 2.4 s behind and getting worse
B: latest-frame thread: first=150 ms, last=176 ms      <- always fresh
```
**Where.** `worker/capture.py`: `LatestFrameCapture`. It also keeps a **keyframe** slot: the newest
frame whose number is a multiple of 3. The AI reads only keyframes, so "every 3rd frame" still holds.
A `threading.Lock` makes sure the AI never reads a half-written slot.
**Judge asks:** *"What if detection is slow?"* Then we analyse fewer frames, but always fresh ones.
The delay never grows.

### 1.3 YOLO results: boxes, classes, confidence
**Plain words.** YOLO looks at a frame once and returns a list of **boxes**. Each box has
`x1, y1, x2, y2` (pixels; top-left and bottom-right corners), a **class** (0 = person, 24 = backpack,
26 = handbag, 28 = suitcase in the COCO list of 80 things) and a **confidence** from 0 to 1.
We ignore anything under 0.5. `imgsz=480` shrinks the frame before the model sees it: faster, slightly
less detail.
**Where.** `worker/detectors.py`.

### 1.4 Tracking IDs (ByteTrack)
**Plain words.** Detection alone says "there are 3 people" in each frame. **Tracking** says "this is
the same person as in the last frame" and gives them an ID number that stays the same while they move.
We need that for every timer rule ("*this person* has been in the zone for 3 s").
`model.track(frame, persist=True)` keeps the tracker's memory between calls; without `persist=True`
every frame would start new IDs. ByteTrack is the tracker built into Ultralytics.
**Where.** `PersonPoseTracker.track()` in `worker/detectors.py`.
**Judge asks:** *"Do you recognise faces?"* No. A track ID is just "person #7 in this camera right
now". It forgets them when they leave. No face recognition, by design.

### 1.5 Pose keypoints and the torso angle
**Plain words.** The pose model (`yolo11n-pose.pt`) gives each person 17 **keypoints** (nose, eyes,
shoulders, elbows, hips, knees, ankles...), each as `(x, y, confidence)`. One model gives us the box,
the track ID and the keypoints, so we do not need a separate person detector.

**Torso angle** = how far the line from mid-shoulders to mid-hips leans away from vertical.
0° = upright, 90° = lying flat. Image `y` grows **downward**.
```python
import math

def torso_angle_deg(kp):
    sx = (kp[5][0] + kp[6][0]) / 2; sy = (kp[5][1] + kp[6][1]) / 2     # shoulders: points 5 and 6
    hx = (kp[11][0] + kp[12][0]) / 2; hy = (kp[11][1] + kp[12][1]) / 2  # hips: points 11 and 12
    return math.degrees(math.atan2(abs(hx - sx), abs(hy - sy)))

def person(shoulder, hip):
    kp = [(0, 0)] * 17; kp[5] = kp[6] = shoulder; kp[11] = kp[12] = hip; return kp

print("standing  :", round(torso_angle_deg(person((100, 100), (100, 300))), 1))
print("bending   :", round(torso_angle_deg(person((100, 150), (160, 300))), 1))
print("lying down:", round(torso_angle_deg(person((100, 300), (300, 310))), 1))
```
Output: `standing: 0.0`, `bending: 21.8`, `lying down: 87.1`.
If a shoulder or hip has confidence below 0.5 (hidden), the function returns `None` instead of guessing.
**Where.** `torso_angle_deg()` and `hip_y()` in `worker/detectors.py`; tests in `tests/test_pose.py`.

### 1.6 MJPEG streaming
**Plain words.** MJPEG = "motion JPEG": the server sends JPEG after JPEG down one HTTP response,
separated by a boundary line. The response type `multipart/x-mixed-replace` tells the browser
"each new part replaces the last", so a plain `<img src=...>` shows live video. No video player needed.
**Where.** `worker/mjpeg.py`, one server per camera on `127.0.0.1:81xx` (only this laptop can reach it).
Later the API's `GET /stream/{id}` passes it on to logged-in users.
Try it: run a worker, open `http://127.0.0.1:8101/stream` in a browser.

### 1.7 Tokens: opaque vs JWT
**Plain words.** After PIN login the server hands the browser a **token**, like a wristband at an
event. The browser shows it with every request (`Authorization: Bearer <token>`).
- **Opaque token**: a random string. Nothing is written on it; the server looks it up in its own
  table to see who it belongs to. Can be cancelled instantly (delete the row).
- **JWT**: the user and role are written *on* the wristband, signed so they cannot be faked.
  The server does not need to look anything up, but anyone can read it and it is hard to cancel early.
```python
import base64, json, secrets
print("opaque:", secrets.token_urlsafe(32))
payload = base64.urlsafe_b64encode(json.dumps({"user": "guard-7", "role": "guard"}).encode()).decode().rstrip("=")
print("JWT payload decodes to:", base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode())
```
Output: a random string like `On2SZ2qdBzVX...` (means nothing), and `{"user": "guard-7", "role": "guard"}`.
**Our choice:** opaque tokens stored (as a hash) in a `sessions` table. Built on Gate 2 day.

---

## Part 2 (20 Oct, Gate 1): rules and incidents

### 2.1 Zones as fractions, and the feet point
**Plain words.** A zone is a polygon drawn on the camera picture. We store its corners as
**fractions** of the frame (x from 0 = left to 1 = right, y from 0 = top to 1 = bottom), so the same
zone works whether the camera sends 640x480 or 1920x1080.

Where is a person? Not the middle of their box: their **feet**, the bottom-centre of the box. Someone
standing *next to* a zone can have their head or arm over it; their feet tell you where they really are.

**Point in polygon (ray casting).** Shoot an imaginary ray from the point to the right and count how
many polygon edges it crosses. Odd = inside, even = outside. Works for any shape.
```python
def feet_point(box, w, h):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2 / w, y2 / h)

def inside(pt, polygon):
    x, y = pt; hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit

zone = [[0.5, 0.6], [1.0, 0.6], [1.0, 1.0], [0.5, 1.0]]
for name, box in [("walks in", (400, 200, 460, 470)), ("stands next to it", (250, 200, 310, 470)),
                  ("head inside, feet outside", (400, 100, 460, 250))]:
    print(name, inside(feet_point(box, 640, 480), zone))
```
Output: `walks in True`, `stands next to it False`, `head inside, feet outside False`.
**Where.** `feet_point()` and `point_in_polygon()` in `worker/rules.py`. Zones live in the database
(`zones` table), loaded from `config/zones.yaml` by `python -m app.seed`.

### 2.2 Rules as small timers (state machines)
**Plain words.** Most rules are "condition X stayed true for N seconds". So each rule remembers
**when** the condition started, per person (per track ID). Every analysed frame:
- condition true → how long since it started? Long enough → incident.
- condition false → forget the start time (timer resets).

This delay is a feature: it is what stops one odd frame from creating a false alarm.

**Cooldown.** After an incident of a type fires on a camera, the same type cannot fire again on that
camera for 60 s. Otherwise one fire would create an incident every frame (500 incidents).
```python
DWELL_S, COOLDOWN_S = 3.0, 60.0
entered_at = {}; last_fired = None

def update(t, track_id, in_zone):
    global last_fired
    if not in_zone:
        entered_at.pop(track_id, None); return None
    start = entered_at.setdefault(track_id, t)
    if t - start >= DWELL_S and (last_fired is None or t - last_fired >= COOLDOWN_S):
        last_fired = t; return f"INCIDENT at t={t:.1f}s (inside for {t - start:.1f}s)"

for t, in_zone in [(0, False), (1, True), (2, True), (3.5, True), (4, True), (5, False),
                   (6, True), (10, True), (70, True)]:
    print(t, in_zone, update(t, 7, in_zone))
```
Output: nothing until `t=4` (inside since t=1, so 3 s) → INCIDENT. Leaves at t=5 (timer reset),
comes back: at t=10 it has been 4 s but the cooldown blocks it. At t=70 the cooldown is over → INCIDENT.

**Grace period.** Trackers sometimes miss a person for one frame. If a timer reset on every missed
frame, nobody would ever reach 3 s. So a timer survives up to `track_grace_s` (1 s) without seeing
the person. Test: `test_brief_track_loss_within_grace_keeps_the_timer`.
**Where.** `DwellTimers` and `RulesEngine._apply_cooldown()` in `worker/rules.py`.

### 2.3 Night window and time zones
**Plain words.** We **store** every time in UTC (one world clock, no daylight-saving confusion) and
**show** it in IST on screen. IST = UTC + 5:30. The night window 20:00–06:00 crosses midnight, so the
usual test `start <= now < end` is wrong; it must be `now >= 20:00 OR now < 06:00`.
```python
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

def is_night(utc_dt, start=time(20, 0), end=time(6, 0), tz="Asia/Kolkata"):
    local = utc_dt.astimezone(ZoneInfo(tz)).time()
    return local >= start or local < end if start > end else start <= local < end

for utc in ["2026-10-20T13:00:00", "2026-10-20T15:00:00", "2026-10-20T23:00:00", "2026-10-21T01:00:00"]:
    dt = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc)
    print(utc, "UTC =", dt.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "IST night =", is_night(dt))
```
Output: 18:30 IST False, 20:30 IST True, 04:30 IST True, 06:30 IST False.
**Where.** `is_night()` in `worker/rules.py`: restricted entry becomes `critical` at night.
On Windows, `zoneinfo` needs the `tzdata` package; it is in `requirements.txt`.

### 2.4 The five rules we have now
All thresholds are in `app/config.py` (overridable in `.env`), never hard-coded in the rules.

| Rule | Severity | How it decides | Setting |
|---|---|---|---|
| access | high, critical at night | feet inside a `restricted` zone for 3 s | `access_dwell_s` |
| loitering | low | same track ID in the same zone for 120 s | `loiter_s` |
| crowding | high | more people with feet in a zone than its `max_people` | `max_people` per zone |
| fall | critical | down for 5 s (see below) | `fall_angle_deg`, `fall_hip_drop`, `fall_down_s` |
| unattended | high | bag still for 60 s with no person within reach | `unattended_s`, `bag_reach_frac`, `bag_still_frac` |

**Fall vs bending.** "Down" needs **both**: torso tilted 60°+ from vertical, **and** hips dropped by at
least 25% of the person's upright height. While someone is upright (torso under 30°) we remember
their hip height. Bending over tilts the torso but the hips stay up, so it is not a fall
(`test_bending_is_not_a_fall`). Getting up within 5 s is not a fall either. If we never saw the
person upright (first seen already on the floor), a box wider than it is tall counts as lying.

**Unattended bag.** Bags come from the plain `yolo11n.pt` detector (backpack, handbag, suitcase),
run about once a second because bags barely move. A bag is "still" if its centre stays within 3% of
the frame width. "Within reach" = a person's box within 15% of the frame width of the bag.
Both clocks must pass 60 s. If the owner comes back, the alone-clock restarts.

PPE (helmet/vest) and fire/smoke need extra models (see 2.8). They are not built yet.

### 2.5 Ring buffer and clips with FFmpeg
**Plain words.** We want a clip from 5 s **before** the incident, but we only know an incident
happened after it happened. So we always keep the last 12 s of video in a **ring buffer**: new frames
go in at one end, the oldest fall out of the other. When an incident fires, we wait 5 s, then cut
from 5 s before to 5 s after.

To save memory, frames are shrunk to 640 px wide and stored as JPEG (12 s is a few MB instead of ~1 GB).
**FFmpeg** then turns those JPEGs into an MP4. Browsers only play it reliably with these settings:
`-c:v libx264` (H.264 video), `-pix_fmt yuv420p` (the colour format browsers support) and
`-movflags +faststart` (index at the front, so playback starts before the whole file downloads).
```python
import collections, subprocess
import cv2, numpy as np

FPS, KEEP_S = 10, 3
ring = collections.deque(maxlen=FPS * KEEP_S)
for i in range(100):
    img = np.full((120, 160, 3), (i * 2) % 255, np.uint8)
    ring.append((i / FPS, cv2.imencode(".jpg", img)[1].tobytes()))
print("frames kept:", len(ring), "oldest t =", ring[0][0], "newest t =", ring[-1][0])

cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg",
       "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "demo.mp4"]
subprocess.run(cmd, input=b"".join(jpg for _, jpg in ring), check=True)
```
Output: `frames kept: 30 oldest t = 7.0 newest t = 9.9`, then `demo.mp4` is H.264, yuv420p, 30 frames.
**Where.** `worker/buffer.py` (`RingBuffer`, `write_clip`). The capture thread feeds every frame in.

### 2.6 The incident flow, end to end
```
capture thread --keyframe--> pose tracker (+ bags ~1/s) --> RulesEngine.update()
     |                                                          | candidate
     +--every frame--> RingBuffer                               v
                                         IncidentReporter.report()
                                           1. INSERT incident + "detected" timeline row
                                           2. save snapshot media/<id>.jpg
                                           3. POST /internal/incidents/<id>/created  (X-Internal-Key)
                                                --> API loads it, sends it on /ws/events
                                           4. (5 s later) clip media/<id>.mp4 from the ring buffer
```
Measured in the Gate 1 run on the test video (this sandbox, not your laptop):
incident confirmed after 3.1 s in the zone → stored in **19 ms** → API told **134 ms** later →
clip written once the 5 s after-window had passed (138 frames, 2.3 s to encode).

Why this order: the dashboard must hear about it in under 2 s, so we never wait for the clip first.
`clip_url` points at a file that appears a few seconds later. If FFmpeg fails, `clip_path` is set
back to empty and the dashboard shows only the snapshot.

**Every failure is caught.** If the database, the disk or the API is down, the worker logs it and
keeps detecting. API calls have a 5 s timeout and one retry.

**Structured logs.** Every step logs `incident=<id> step=<name> ms=<time>`, so a slow step is found
by numbers, not guesses.

**Where.** `worker/incidents.py`, `app/routers/internal.py`, `app/ws.py`, `app/schemas.py`
(`IncidentOut` turns a database row into the exact contract JSON, with times like `2026-10-24T10:12:41Z`).

### 2.7 Tests: fake detections in, expected incidents out
**Plain words.** A rule test does not need a camera or a model. We invent the detections
("person 1 stands here from t=0 to t=5") and check which incidents come out and when.
Example: `test_access_fires_after_three_seconds_in_restricted_zone` feeds a person in the zone every
0.3 s and expects exactly one `access` incident at about 3 s.

Tests that need the database use a **separate test database** (`safetyai_test`), so they never touch
real incidents. `tests/conftest.py` points the app at it, migrates it once, and empties it before
each test. If it is not reachable those tests are skipped, not failed.

Run: `pytest` (all), `pytest tests/test_rules.py -v` (one file, with names).

### 2.8 PPE and fire/smoke models (status: not started)
**Plain words.** The models we use know 80 everyday objects. They do not know "helmet", "safety vest"
or "fire". For those we need a model trained on labelled photos of exactly those things.
Two ways: find one someone already trained, or train our own on Google Colab's free GPU from a
labelled dataset (e.g. from Roboflow). Either way, the model **and** the dataset each have a
**licence** (the legal permission to use them); some say "research only" or "non-commercial", and we
must write each one in `docs/disclosures.md`. Our current models are AGPL-3.0, for example.
Roadmap deadline: decide by 21 Oct; fallback is the best public model with its licence noted.

---

## Commands for your Windows laptop (PowerShell, from `backend\`)
```powershell
# once: FFmpeg on PATH (needed for clips), then reopen PowerShell
winget install Gyan.FFmpeg
ffmpeg -version

# after every pull: install anything new (tzdata gives Windows the IST time zone)
pip install -r requirements.txt

# database: migrate, then load cameras and zones
alembic upgrade head
python -m app.seed

# once: the test database (pytest uses it; your real data is never touched)
psql -U postgres -c "CREATE DATABASE safetyai_test OWNER <your db user>;"
pytest

# run: API in one window, one worker per camera in others
uvicorn app.main:app --reload --port 8000
python -m worker.run_worker --camera cam-webcam
# test with a recorded clip instead of the webcam:
python -m worker.run_worker --camera cam-webcam --source C:\path\to\clip.mp4
```
