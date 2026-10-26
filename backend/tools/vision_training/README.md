# Training the PPE, fire/smoke and rider-helmet models (Google Colab)

The worker already has the rules; each stays off until its model file is set in `.env`:
- PPE: a worker in a PPE zone without a helmet or vest (low, WhatsApp)
- fire: flames in 5 analysed frames in a row (critical, orange in the app)
- smoke: smoke in 5 analysed frames in a row (high, grey in the app)
- rider without a helmet: on a gate camera, in 4 frames in a row (calls that gate's guard only)

| Model | Dataset | Classes we use | Colab time (T4) |
|---|---|---|---|
| `models/ppe.pt` | Ultralytics **Construction-PPE** (1,416 images, downloads by itself) | `helmet`, `vest` | about 15 min |
| `models/fire_smoke.pt` | **D-Fire** (about 21,000 images) **+ your own lighter photos** (Part 3b) | `fire`, `smoke` | about 1.5 to 2 h |
| `models/rider_helmet.pt` | a two-wheeler helmet set from Roboflow Universe (Part 5) | `with helmet`, `without helmet` | about 30 min |

Do PPE first: it is quick and checks the whole route end to end.

**Will a lighter's flame set off the alarm?** With D-Fire alone, maybe not: D-Fire is mostly big fires and
smoke filmed from far away, and a lighter's flame is tiny and different. So do Part 3b: film the lighter with
YOUR phone camera at the distance you will use on stage, label those photos as `fire`, and train with them.
The rule also takes flames from 0.35 confidence (`FIRE_CONF`), lower than other models, for small flames.
Then check it live (Part 4, step 5) before the event.

---

## Part 1: open Colab with a GPU (2 minutes)

1. Go to https://colab.research.google.com → **New notebook**.
2. **Runtime → Change runtime type → T4 GPU → Save**.
3. First cell, run it:
   ```python
   !pip install -q ultralytics roboflow
   import ultralytics; ultralytics.checks()
   ```
   The last line should mention `CUDA` and `Tesla T4`. (The GPU is only on Colab; the laptop never uses CUDA.)

## Part 2: PPE model (about 15 minutes)

New cell, run it:
```python
from ultralytics import YOLO
model = YOLO("yolo11n.pt")
model.train(data="construction-ppe.yaml", epochs=60, imgsz=480, batch=32, patience=15, name="ppe")
```
The dataset downloads itself the first time. When it finishes, the last lines show `mAP50` per class; `helmet`
and `vest` around 0.7 or higher are fine for the demo.

Download: folder icon on the left → `runs/detect/ppe/weights/best.pt` → right-click → **Download**.
Rename it to **`ppe.pt`**.

## Part 3: fire/smoke model (about 2 hours)

1. Open the D-Fire copy on Roboflow Universe: https://universe.roboflow.com/uweeaai/d-fire-ehbj2
   (sign in with Google; free).
2. **Download Dataset → Format: YOLOv11 → Show download code → Jupyter**. Copy the code it shows (it
   contains your own Roboflow key: never paste it into our repo or chat).
3. New Colab cell: paste that code and run it. It ends by printing a folder name, e.g. `D-Fire-1`.
4. New cell (change the folder name if yours differs):
   ```python
   from ultralytics import YOLO
   model = YOLO("yolo11n.pt")
   model.train(data="D-Fire-1/data.yaml", epochs=40, imgsz=480, batch=32, patience=10, name="fire")
   ```
   In a hurry: `epochs=20` (about 1 h). If Colab disconnects, run
   `YOLO("runs/detect/fire/weights/last.pt").train(resume=True)`.
5. Download `runs/detect/fire/weights/best.pt` and rename it **`fire_smoke.pt`**.

Check the class names: open `D-Fire-1/data.yaml` in Colab; the `names:` line should say `fire` and `smoke`
(any order). If they differ, put the real names in `FIRE_LABELS` / `SMOKE_LABELS` below.

## Part 3b: teach it YOUR lighter (about 45 minutes, do this before Part 3's training)

1. **Film it.** Put the phone (IP Webcam, or the normal camera app) where it will stand on stage. Film 1 to 2
   minutes of the lighter's flame: near and far (30 cm to 2 m), left, right, moving, against a wall, against a
   window, with a person holding it. Also film 30 s with the lighter off (no flame), and orange or bright
   things that are NOT fire (a lamp, an orange shirt): these teach it what to ignore.
2. **Cut it into photos** (on the laptop, from the folder with the videos):
   ```powershell
   mkdir lighter_frames
   ffmpeg -i lighter.mp4 -vf fps=2 lighter_frames/lighter_%04d.jpg
   ffmpeg -i not_fire.mp4 -vf fps=1 lighter_frames/notfire_%04d.jpg
   ```
   About 200 to 300 lighter photos is good.
3. **Label them** on Roboflow: in the D-Fire page click **Fork Dataset** (it becomes your own project), then
   **Upload** → drop the `lighter_frames` folder → **Annotate**: draw a box around each flame, class `fire`.
   Leave the not-fire photos with no boxes (Roboflow calls them "null" images; keep them).
4. **Generate** a new version (Train/Valid/Test split 80/10/10, resize to 480) and use **its** download code in
   Part 3, step 2. Then train exactly as in Part 3.

## Part 5: rider without a helmet (gate cameras, about 30 minutes)

1. On https://universe.roboflow.com search **"bike helmet detection"** or **"helmet detection two wheeler"**.
   Pick a dataset whose classes are like **With Helmet / Without Helmet** (photos of people on motorbikes and
   scooters, a few thousand images). Check its licence on its page.
2. Download it as YOLOv11 (Jupyter code) in Colab and train:
   ```python
   model = YOLO("yolo11n.pt")
   model.train(data="<folder>/data.yaml", epochs=40, imgsz=480, batch=32, patience=10, name="helmet")
   ```
3. Download `runs/detect/helmet/weights/best.pt` as **`rider_helmet.pt`**.
4. Open its `data.yaml`: the class for a bare head must be one of `without helmet`, `no helmet`, `no_helmet`,
   `nohelmet` (any case), or put its real name in `NO_HELMET_LABELS`.
5. Best results: add 100 or so photos from YOUR gate camera (Part 3b's steps, classes `with helmet` /
   `without helmet`).

## Part 4: use them on the laptop (10 minutes)

1. Put both files in `C:\project folder\kraken-001\backend\models\` (not in git).
2. Make the fast Intel versions, from `backend\` (the worker picks them up automatically):
   ```powershell
   yolo export model=models/ppe.pt format=openvino imgsz=480
   yolo export model=models/fire_smoke.pt format=openvino imgsz=480
   yolo export model=models/rider_helmet.pt format=openvino imgsz=480
   ```
3. In `backend\.env`:
   ```
   PPE_MODEL_PATH=models/ppe.pt
   FIRE_MODEL_PATH=models/fire_smoke.pt
   PPE_HELMET_LABELS=helmet
   PPE_VEST_LABELS=vest
   FIRE_LABELS=fire,flame
   SMOKE_LABELS=smoke
   FIRE_CONF=0.35
   HELMET_MODEL_PATH=models/rider_helmet.pt
   ```
   **Gate guards:** the guard who is called for a rider without a helmet is the Guard who has that gate camera
   (People and roles → their cameras). Main gate camera → main gate guard, second gate → second gate guard, and
   so on. Put each gate guard's zone to the gate's zone name to be exact; nobody on duty with that camera →
   that camera's Head Guard is called.
4. Restart the API (it restarts the camera workers). Quick tests:
   - **PPE:** stand in the entrance PPE zone without a helmet for about 3 s → a low `ppe` incident.
   - **Fire:** play a fire video on a phone in front of a camera (never a real flame indoors) → a critical
     `fire` incident and the call ladder.
   - Single-image check without a camera: `yolo predict model=models/fire_smoke.pt source=some_fire.jpg imgsz=480`
     saves a picture with boxes in `runs/detect/predict`.
5. **The lighter test, live, before the event** (a window opens with boxes drawn; press Q to close):
   ```powershell
   yolo predict model=models/fire_smoke.pt source=http://PHONE-IP:8080/video imgsz=480 conf=0.35 show=True
   ```
   Light the lighter in front of the phone at stage distance. You want a `fire` box on most frames with a
   score above 0.35. If it flickers on and off, film more lighter photos (Part 3b) and train again.
   Tips: keep a plain wall behind the flame, not a bright window; in IP Webcam lower the exposure so the
   flame does not turn into a white blob.

## Tuning

- Too many false fires (sunlight, orange clothes): raise `FIRE_FRAMES` (e.g. 8) before touching confidence.
- A lighter is missed: lower `FIRE_CONF` to 0.3 and `FIRE_FRAMES` to 3, and add more of your own photos.
- PPE misses helmets far away: cameras closer to the door work better than a lower threshold.
- Speed: each extra model costs a few ms per analysed frame; turn a model on only for the cameras that need it
  (PPE zones exist only where they are drawn in `zones.yaml`).

---

## Disclosure (add to docs/disclosures.md when trained)

- Ultralytics YOLO11n (AGPL-3.0), fine-tuned on Google Colab (free T4 GPU).
- Construction-PPE dataset (Ultralytics; licence on its page: https://docs.ultralytics.com/datasets/detect/construction-ppe).
- D-Fire dataset (Venâncio, Lisboa, Barbosa, Neural Computing and Applications, 2022; https://github.com/wizbeans/FireDataset),
  downloaded from the Roboflow Universe copy (listed as CC BY 4.0). Check the original licence before public use.
