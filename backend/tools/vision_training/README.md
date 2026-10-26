# Training the PPE and fire/smoke models (Google Colab)

The worker already has the PPE rule (no helmet or no vest in a PPE zone) and the fire rule (fire or smoke
in 5 analysed frames). Both stay off until a model file is set in `.env`. This guide trains two small
YOLO11n detectors on Colab's free GPU and plugs them in.

| Model | Dataset | Classes we use | Colab time (T4) |
|---|---|---|---|
| `models/ppe.pt` | Ultralytics **Construction-PPE** (1,416 images, downloads by itself) | `helmet`, `vest` | about 15 min |
| `models/fire_smoke.pt` | **D-Fire** (about 21,000 images, fire + smoke) | `fire`, `smoke` | about 1.5 to 2 h |

Do PPE first: it is quick and checks the whole route end to end.

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
(any order). If they differ, put the real names in `FIRE_LABELS` below.

## Part 4: use them on the laptop (10 minutes)

1. Put both files in `C:\project folder\kraken-001\backend\models\` (not in git).
2. Make the fast Intel versions, from `backend\` (the worker picks them up automatically):
   ```powershell
   yolo export model=models/ppe.pt format=openvino imgsz=480
   yolo export model=models/fire_smoke.pt format=openvino imgsz=480
   ```
3. In `backend\.env`:
   ```
   PPE_MODEL_PATH=models/ppe.pt
   FIRE_MODEL_PATH=models/fire_smoke.pt
   PPE_HELMET_LABELS=helmet
   PPE_VEST_LABELS=vest
   FIRE_LABELS=fire,smoke
   ```
4. Restart the API (it restarts the camera workers). Quick tests:
   - **PPE:** stand in the entrance PPE zone without a helmet for about 3 s → a low `ppe` incident.
   - **Fire:** play a fire video on a phone in front of a camera (never a real flame indoors) → a critical
     `fire` incident and the call ladder.
   - Single-image check without a camera: `yolo predict model=models/fire_smoke.pt source=some_fire.jpg imgsz=480`
     saves a picture with boxes in `runs/detect/predict`.

## Tuning

- Too many false fires (sunlight, orange clothes): raise `FIRE_FRAMES` (e.g. 8) before touching confidence.
- PPE misses helmets far away: cameras closer to the door work better than a lower threshold.
- Speed: each extra model costs a few ms per analysed frame; turn a model on only for the cameras that need it
  (PPE zones exist only where they are drawn in `zones.yaml`).

---

## Disclosure (add to docs/disclosures.md when trained)

- Ultralytics YOLO11n (AGPL-3.0), fine-tuned on Google Colab (free T4 GPU).
- Construction-PPE dataset (Ultralytics; licence on its page: https://docs.ultralytics.com/datasets/detect/construction-ppe).
- D-Fire dataset (Venâncio, Lisboa, Barbosa, Neural Computing and Applications, 2022; https://github.com/wizbeans/FireDataset),
  downloaded from the Roboflow Universe copy (listed as CC BY 4.0). Check the original licence before public use.
