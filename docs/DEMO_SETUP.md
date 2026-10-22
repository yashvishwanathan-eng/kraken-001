# Demo setup: 3 cameras (2 phones + 1 laptop)

This guide sets up the stage demo. Your laptop is the "brain": it runs the API, the AI and the dashboard. The other three devices are only cameras.

```
 Phone 1  (Restricted Door)  ─┐
 Phone 2  (Shop Floor)        ├── your laptop's Wi-Fi hotspot ──►  YOUR LAPTOP
 Laptop 2 (Entrance)         ─┘                                    API + 3 workers + dashboard
```

| Camera | Device | id in the system | What it shows on stage |
|---|---|---|---|
| 1 | Android phone 1 | `cam-door` | Someone walks to the door: **restricted entry** (critical at night) |
| 2 | Android phone 2 | `cam-floor` | Someone falls: **fall**. Someone leaves a bag: **unattended bag**. **Fire/smoke** once a fire model is chosen |
| 3 | Laptop 2 | `cam-entrance` | 4 people walk in together: **crowding**. Helmet check on the right half, once a PPE model is chosen |

There is also `cam-webcam`, your own laptop's webcam. It's only for testing at home when the phones aren't around.

---

## Step 1: Make your own network (most important)
College Wi-Fi often stops devices from seeing each other, so bring your own network.

1. On **your laptop**: Settings → Network & internet → **Mobile hotspot** → On.
2. Note the hotspot name and password.
3. Connect phone 1, phone 2 and laptop 2 to that hotspot.

On Windows hotspots, devices usually get addresses like `192.168.137.x`.

> Example: phone 1 gets `192.168.137.23`, phone 2 gets `192.168.137.41`, laptop 2 gets `192.168.137.88`.

## Step 2: Phones (do this on both)
1. Install **IP Webcam** (by Pavel Khlebovich) from the Play Store. It's free.
2. Open it and change these settings first:
   - **Video resolution:** 640×480 (or 1280×720). Higher only slows your laptop.
   - **FPS limit:** 25–30. The system looks at every 3rd picture, so 25 gives about 8 checks a second.
   - **Main camera:** the back camera. Use the wide one if your phone has it.
   - **Prevent sleep:** on. Then dim the screen, which stops the phone overheating.
3. Scroll down and tap **Start server**.
4. The screen shows an address like `http://192.168.137.23:8080`. Write it down.
5. Check it: on your laptop, open `http://192.168.137.23:8080/video` in a browser. You should see live video.

Keep both phones on chargers and on stands. **A camera must not move after you draw its zones.**

## Step 3: Laptop 2
1. Install Python 3.11 and then OpenCV: `pip install opencv-python`
2. Copy just one file to it: `backend/tools/camera_share.py`
3. Run:
   ```powershell
   python camera_share.py
   ```
4. It prints something like `sharing camera 0 at http://192.168.137.88:8090/video`.
5. If Windows asks about the firewall, allow **Private networks**.
6. Check it from your laptop's browser: `http://192.168.137.88:8090/video`.

If laptop 2 has a second (USB) webcam you'd rather use: `python camera_share.py --source 1`.

## Step 4: Put the addresses into your laptop
Open `backend/config/cameras.yaml` on **your** laptop and replace the three placeholders:

```yaml
  - id: cam-door
    source: "http://192.168.137.23:8080/video"   # was PHONE1-IP
  - id: cam-floor
    source: "http://192.168.137.41:8080/video"   # was PHONE2-IP
  - id: cam-entrance
    source: "http://192.168.137.88:8090/video"   # was LAPTOP2-IP
```

Keep the `/video` at the end. The phones use port `8080`, and laptop 2 uses `8090`.

The addresses can change when a device reconnects. On the day, check them again before the demo.

## Step 5: Start everything (on your laptop, from `backend/`)
Open one PowerShell window for each line:

```powershell
python -m app.seed                                   # once, after changing the yaml files
uvicorn app.main:app --port 8000                     # window 1: the API
python -m worker.run_worker --camera cam-door        # window 2
python -m worker.run_worker --camera cam-floor       # window 3
python -m worker.run_worker --camera cam-entrance    # window 4
```

Each worker window prints a line like:
```
[cam-door] analysed 8.1 fps | camera 25.0 fps | persons=1 | detect 30 ms
```
- **analysed fps:** how many times a second the AI checks the picture. Aim for 6 or more.
- **camera fps:** what the phone or laptop is sending.
- **persons:** how many people it sees right now.

Open `http://localhost:8000/health`: all three cameras should say `"online": true`.

## Step 6: Fit the zones to the real view
A zone is a box drawn on the picture, written as fractions: `x` from 0 (left) to 1 (right), `y` from 0 (top) to 1 (bottom). A person is "in" a zone when their **feet** are inside it.

Example for the door camera. The door is on the right side of the picture, and the floor in front of it covers the right third of the bottom half:

```yaml
  cam-door:
    - name: server-room-door
      kind: restricted
      polygon: [[0.65, 0.50], [1.00, 0.50], [1.00, 1.00], [0.65, 1.00]]
```

How to find the numbers:
1. Look at the live picture.
2. Estimate where the floor in front of the door starts and ends. For example, "it starts at 65% from the left and halfway down".
3. Write those as the four corners.
4. Run `python -m app.seed` again. It updates the zones, and removes any zone you deleted from the file.
5. Restart that camera's worker.

Things to know:
- **Falls, bags and fire** are checked on the whole picture. They need no zone.
- **Crowding** uses `max_people` on the `entrance` zone, currently 3. So 4 people at once raises the alert. Change it to suit how many people you have on stage.

## Step 7: Rehearse each incident
| Incident | How to trigger it | It fires after |
|---|---|---|
| Restricted entry | Stand with your feet in the door zone | 3 seconds |
| Fall | Lie down flat on the floor (not just bend) | 5 seconds lying down |
| Unattended bag | Put a backpack down and walk out of the picture | 60 seconds |
| Crowding | 4 people stand in front of laptop 2 | Right away |
| Loitering | Stay in the shop-floor zone | 2 minutes |

The same incident type on the same camera can't fire again for 60 seconds. If nothing happens on a second try, wait a minute.

For rehearsals without real phone calls, set `CALLS_MODE=simulate` and `WHATSAPP_MODE=simulate` in `.env`. Everything shows on the dashboard as if calls were made.

## Speed tips (three cameras on one laptop)
- **Export the models** once (see `TECHNICAL.md` section 9). This makes detection about 2x faster.
- **Plug in your laptop** and set Windows to "Best performance". On battery, the CPU slows down.
- **If analysed fps drops below 5** on every camera, try these in order:
  1. Lower the phones to 640×480.
  2. Close other apps.
  3. As a last resort, set `DETECT_EVERY_N_FRAMES=4` in `.env`. This makes fewer checks, so incidents are confirmed a little later.

## If something goes wrong
| Problem | Fix |
|---|---|
| Browser can't open the phone address | Phone and laptop aren't on the same hotspot, or the IP Webcam server stopped. Tap Start server again |
| Worker says it can't open the camera | Check the address in `cameras.yaml`, including `/video`. It retries every 5 seconds, so just fix the address and restart the worker |
| Camera online, then offline | Phone slept or overheated. Turn on "Prevent sleep", plug in the charger, and take the phone out of direct sun |
| Laptop 2 shows "camera 0 not available" | Another app (Zoom, Teams, the Camera app) is using the webcam. Close it |
| A zone fires at the wrong place | The camera moved, or the numbers are off. Redo step 6 |
| Seed prints "camera cam-phone is in the database but no longer in cameras.yaml" | Harmless. It's the old test camera, kept so its old incidents aren't deleted |
