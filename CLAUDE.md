# CLAUDE.md: SafetyAI backend

## Project
Hackathon build: "AI Video Intelligence and Workplace Safety System" (Sustainathon, VIT Bengaluru, event 26 Oct 2026; everything finished by 25 Oct). Product name is a placeholder ("SafetyAI"), kept in ONE config constant.
An on-premise system that watches CCTV cameras, turns detections into confirmed incidents, saves a clip and snapshot per incident, phones the right security guard with an escalation ladder, and answers typed or spoken questions about incidents.
Pitch: "Every factory has cameras nobody watches. Most AI adds a dashboard nobody watches. Ours picks up the phone."

## Team and boundaries
- I (Kirito) own the whole project: `backend/` (vision, rules engine, API, database, calls, AI assistant) and, since 26 Oct, `frontend/` too.
- `frontend/` (React 19 + Vite + Tailwind + framer-motion, brief in `frontend/BRIEF.md`) was built by a teammate in the VIT-kraken repo (commit 6b3ad51) and moved here on 26 Oct. We now upgrade it ourselves; both halves live in this repo.
- The API contract is `docs/CONTRACT.md`. It is the single source of truth for backend and frontend. Never change it silently: when an endpoint changes, update the contract, the backend and the frontend in the same change, and tell me.

## Working rules
1. Before writing code, read the relevant existing files. Never guess field names, paths or endpoints. Check `docs/CONTRACT.md`.
2. Show a short plan (files to create/change) before coding.
3. Change only what the task asks; no unrelated refactors.
4. Before saying a task is done: run `pytest` (when tests exist) and start the API once to check it boots. Report results.
5. Every Pydantic request/response model is typed; bad input returns a clear 422, never a 500.
6. Secrets only in `.env`; keep `.env.example` updated with empty values.
7. Every model, dataset, library and external API used gets a line in `docs/disclosures.md` (event rule 9).
8. Machine: Windows 11, Intel i7-13620H, 16 GB RAM, NO NVIDIA GPU. Never suggest CUDA. Commands in PowerShell.
9. Learning first: Kirito is new to computer vision and async code. Before building a new concept, explain it in plain words and run a tiny example in the scratchpad (never in the repo). After building, explain what was built and why, file by file.
10. Branches: day work goes on the session's branch (now `claude/busy-tesla-org5z1`). Do not use `main` for day work. Since 26 Oct Kirito does not need to approve pushes: commit and push when a piece of work is done and its checks pass, then say what was pushed.

## Stack
Python 3.11 · FastAPI · Uvicorn · SQLAlchemy 2 · Alembic · PostgreSQL (local) · APScheduler · httpx
Vision: Ultralytics YOLO (`yolo11n-pose.pt` for person boxes, track IDs and keypoints; built-in ByteTrack) · OpenCV · OpenVINO export · FFmpeg
Calls: Twilio voice + WhatsApp (ported from my Bhoomi project) · ngrok for webhooks
AI: Groq (LLM JSON mode + Whisper) online · Ollama `llama3.2:3b` + faster-whisper offline

## Folder structure
```
backend/
  app/
    main.py            app, CORS (allow http://localhost:5173), routers, static /media
    config.py          settings from .env (pydantic-settings)
    db.py              engine, session
    models.py          SQLAlchemy models
    schemas.py         Pydantic models (mirror docs/CONTRACT.md exactly)
    ws.py              WebSocket connection manager
    deps.py            FastAPI dependencies: current_user (401) and require(action) (403)
    seed.py            python -m app.seed: loads cameras.yaml + zones.yaml (+ demo users) into the database; safe to rerun
    routers/           health, cameras, stream, events, zones, responders, broadcast, assistant, stats, twilio, internal, auth, users, roll_call (/alerts/all + /roll-call), audit, share (signed media for WhatsApp/Twilio), roles, duty (QR check-in), overview (+ /usage), activity
    services/          escalation.py (ladders, digest, heartbeat watchdog), calls.py (Twilio + 1/s queue), whatsapp.py, alerts.py (alert everybody, roll call),
                       incidents.py (ack/close shared by dashboard, phone, assistant), directory.py (who to call), assistant.py (question -> filter -> answer),
                       commands.py (laptop commands + confirm), knowledge.py (SOP answers), llm.py (Groq -> Ollama), stt.py (Groq Whisper -> faster-whisper),
                       permissions.py (PINs, sessions, camera scope, signed links), audit.py, camera_status.py (heartbeats),
                       camera_registry.py (cameras from the database, probe), workers.py (starts camera workers), duty.py, roles.py, tts.py (Rumik Silk)
  worker/
    run_worker.py      one process per camera: python -m worker.run_worker --camera cam-east-wing
    capture.py         latest-frame capture thread: reads the camera nonstop, keeps only the newest frame and the newest every-Nth keyframe, reconnects every 5 s
    detectors.py       person detection, tracking and pose (yolo11n-pose), torso angle; PPE and fire models later
    rules.py           all incident rules (pure functions + small state, unit-testable)
    buffer.py          ring buffer + clip writer
    incidents.py       stores a confirmed incident (row, timeline, snapshot), notifies the API, writes the clip 5 s later
    heartbeat.py       posts online + analysed FPS to /internal/heartbeat every 5 s
    mjpeg.py           annotated MJPEG server on 127.0.0.1:81xx
    (app) services/conversations.py + routers/conversations.py: history of every talk with Friday, calls with audio (P31)
    recorder.py        all-day recording: 5-minute MP4 files per camera in media/recordings/, steady fps from the newest frame, 7 days kept (P30)
  config/cameras.yaml  cameras: id, name, source (0, http://PHONE-IP:8080/video, file path, rtsp://), port. Demo: cam-door (phone 1), cam-floor (phone 2), cam-entrance (laptop 2), cam-webcam (home testing)
  config/zones.yaml    demo zones per camera; polygon points are fractions of the frame (0..1); max_people for crowding
  config/demo_users.yaml  the 4 demo users (role, cameras, actions); PINs come from DEMO_USER_PINS in .env
  config/demo_responders.yaml  call roster (zone, shift, call order); phones come from DEMO_RESPONDER_PHONES in .env
  knowledge/           SOP and contact documents (.md/.txt) the assistant answers from; SAMPLE-sop.md is invented demo content
  alembic/
  tools/camera_share.py  runs on the second laptop: shares its webcam as MJPEG at :8090/video (Python + OpenCV only)
  tests/               conftest.py points tests at a separate `<db>_test` database; tests/fixtures/ is a frozen copy of config/ so demo edits never break tests
  models/              model weights + OpenVINO exports (gitignored); workers use <name>_openvino_model/ when present
  media/               clips and snapshots (gitignored)
frontend/              React dashboard (npm ci ; npm run dev on :5173; VITE_API_URL and VITE_USE_MOCKS in frontend/.env)
docs/
  CONTRACT.md          API contract: endpoints, shapes, incident object, enums, auth rules
  GUIDE.md             start here: every topic from zero (simple, deeper, where, try it, check yourself) + first full run
  STUDY.md             Kirito's study notes: every concept with a tiny example, updated as we build
  READ_FIRST.md        the simple, non-technical explanation of the whole system (read before anything else)
  TECHNICAL.md         the full technical reference: every module, flow, setting, test, run and fix
  DEMO_SETUP.md        stage setup: 2 Android phones (IP Webcam) + laptop 2 as cameras on the laptop's own hotspot
  disclosures.md
```

## How the parts talk
- Each camera runs its own worker process. The API never runs detection inside a request.
- Worker serves annotated MJPEG on its own port; `GET /stream/{camera_id}?st=<token>` proxies it (httpx streaming). `/cameras` returns the signed `stream_url`; the token is 1 hour and checked against the user's delegated cameras.
- Worker confirms an incident, saves clip + snapshot, inserts the row, then calls `POST /internal/incidents/{id}/created` on the API (header `X-Internal-Key`). The API broadcasts it on `/ws/events` and starts escalation.
- Worker sends `POST /internal/heartbeat` (proposed). If a camera goes silent for 30 s, the API WhatsApps the Admin.
- Latest-frame pattern: the capture thread reads the camera nonstop and overwrites one slot with the newest frame, so frames never queue up behind a slow detector. Detection only ever reads from that slot. Every Nth frame (`detect_every_n_frames`) is also kept as the keyframe that gets analysed. Without this, a slow detector analyses frames that are seconds old.
- The capture thread reconnects to a lost camera every 5 s and reports online/offline + FPS; `/health` shows each camera.
- Models load once at startup and run one warm-up frame.

## API contract
See `docs/CONTRACT.md`. Endpoints, request and response shapes, the incident object, enums, timeline actions, and auth rules live there only. Do not copy them into this file.

## Incident rules (rules.py; thresholds in config, not hard-coded)
| Type | Severity | Fires when |
|---|---|---|
| access | high (critical 20:00–06:00 IST) | person's FEET point (bottom-centre of box) inside a restricted zone for 3 s |
| ppe | low | person in a ppe zone without helmet or vest in 10 consecutive analysed frames |
| fall | critical | pose torso near-horizontal + hips dropped, and stays down 5 s (bending is not a fall) |
| fire | critical | fire or smoke in 5 consecutive analysed frames, confidence ≥ 0.5 |
| unattended | high | backpack/handbag/suitcase still for 60 s with no person within reach |
| loitering | low | same track ID in one zone for 120 s |
| crowding | high | more than N people in a zone (N per zone) |
- Detection confidence threshold 0.5. Cooldown: same camera + type cannot fire again within 60 s.
- Analyse every 3rd frame, `imgsz=480`, OpenVINO models when exported.
- Clip: 5 s before + 5 s after from the ring buffer, re-encoded with FFmpeg `-c:v libx264 -pix_fmt yuv420p -movflags +faststart` so browsers play it.

## Config values (config.py; overridable from .env; not secrets)
- `NIGHT_START=20:00`, `NIGHT_END=06:00`, `TIMEZONE=Asia/Kolkata` (IST). Restricted entry in this window is critical.
- `DIGEST_TIME=18:00` IST. Daily digest of low-severity incidents.
- `HEARTBEAT_TIMEOUT_S=30`.
- `STREAM_TOKEN_TTL_S=3600` (signed stream URL lifetime).
- `SESSION_TTL_H=12` (a login lasts one shift). `LOGIN_MAX_FAILURES=5` per `LOGIN_WINDOW_S=60`.
- `PREFER_OPENVINO=true` (use the OpenVINO export when it exists; about 2x faster on Intel CPUs).
- `CALLS_MODE` / `WHATSAPP_MODE`: `auto` (live when the keys are set, else off), `live`, `simulate` (pretend; for demos and tests), `off`.
- `TWILIO_CPS=1` (new calls per second), `CALL_RING_TIMEOUT_S=30`, `CALL_LANGUAGE=en-IN`. On-screen grace: `ONSCREEN_GRACE_S=5` (replaces the old 60 s / 2 min ladder delays).
- Role labels the ladder looks for: `ROLE_ADMIN=Admin`, `ROLE_MANAGER=Manager`, `ROLE_HEAD_GUARD=Head Guard`, `ROLE_GUARD=Guard`.
- `LLM_TIMEOUT_S=5` (Groq), `OLLAMA_TIMEOUT_S=10`, `ASSISTANT_CONFIRM_TTL_S=60`, `KNOWLEDGE_DIR=knowledge`.
- PPE/fire labels: `PPE_HELMET_LABELS`, `PPE_VEST_LABELS`, `FIRE_LABELS` (class names differ between models), `PPE_MISSING_FRAMES=10`, `FIRE_FRAMES=5`.
- `LOW_REPEAT_COUNT=3`, `LOW_REPEAT_WINDOW_MIN=60`.

## Escalation and calls (confirmed 18 Oct)
Times are cumulative from incident creation. A ladder stops at the first acknowledgement, from any channel.

Routing by severity:
- **Critical** (fire, fall; restricted entry at night):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin (snapshot).
  - 0 s: call the Admin (5 s if the Admin has a dashboard open: time to react on screen; acknowledging cancels the call). Changed 26 Oct.
  - 5 s: call the Manager (10 s if on screen). Changed 26 Oct (was 60 s and 2 min).
  - Never call above Admin.
- **High** (restricted entry in the day, unattended bag, crowding):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin.
  - 0 s: call the Manager (5 s if on screen).
  - 0 s: call the Admin too (5 s if on screen). Changed 26 Oct (P27): the whole hierarchy is called.
- **Nobody answered** (critical and high): still open after `NO_ANSWER_ALERT_ALL_S=45`: every guard on shift gets a WhatsApp and a roll-call phone call that says what happened (P27).
- **Low** (ppe, loitering):
  - No calls.
  - WhatsApp to the zone manager: the Manager role delegated to that zone. Confirmed.
  - Daily digest at 18:00 IST.
  - If the same zone repeats 3 times within 1 hour, call the Head Guard once for that zone.

Call mechanics:
- Alert text is ready BEFORE dialling. Calls open in English with `<Say>` straight away. Tamil and Hindi are not used at the start of a call.
- Since 26 Oct (P29) calls are a conversation with Friday by default (`CALL_STYLE=conversation`, `app/services/call_agent.py`): she says what happened and where, asks where they are, listens and answers (Groq JSON {reply, action}, actions checked by our code), connects the admin on request, reports every reply to the dashboard. The keypad menu below is `CALL_STYLE=menu`.
- `<Gather input="speech dtmf">`: 1 = going (ack), 2 = false alarm, 3 = alert all guards (with confirm), 4 = connect the manager, 9 = repeat (P27). Speech mapped to the same 4 intents (keyword match first, short Groq prompt as fallback).
- Phone acknowledgements (keypad or voice) use the responder linked to the call. They never take the user from the request body.
- **Critical**: voice and keypad can only ACKNOWLEDGE. Closing needs the dashboard.
- **High**: the guard may mark false alarm (logged with name).
- **Low**: the guard may resolve from the dashboard.
- Webhooks are idempotent (a repeated keypress never double-acknowledges). Every step is written to the timeline, the audit log and `/ws/events`.
- Head Guard and Admin can speak or press keys during an alert call.
- Never auto-call fire services or 112.
- Twilio trial: only verified numbers can be called.

## Product updates (18 Oct, confirmed by Kirito)
Decisions from the 18 Oct product review, all confirmed. Escalation details are in the section above; the endpoint list is in `docs/CONTRACT.md`.

1. **Roles and camera delegation.** One permission system, not hard-coded roles. A user has a role label, a list of allowed cameras and a list of allowed actions. Demo seed roles: Admin, Manager, Head Guard, Guard. Each user sees only their delegated cameras and incidents. PIN login on the control-room laptop.
2. **Severity routing.** See "Escalation and calls". There is no CEO role; the top call level is Admin.
3. **Phone commands.** Head Guard and Admin can speak or press keys during an alert call. "Alert everybody" sends an instant WhatsApp to all on-shift guards, shows a red banner on every dashboard, and starts parallel calls. Twilio places about 1 new call per second by default, so a full roll-out takes time. Say so on stage.
4. **Roll call.** Every alerted guard presses 1 = responding or 2 = safe/evacuated. The dashboard shows a live board, e.g. "14 of 20 responded, 6 not reached".
5. **Laptop assistant.** Named **Friday** (26 Oct; was "Jarvis", which must not appear in user-facing text). Female Rumik Silk voice. The product name stays `PRODUCT_NAME`. Wake word "Hey Friday" (our own openWakeWord model trained on Colab, `models/hey_friday.onnx`; the "Hey Jarvis" stand-in was removed 26 Oct).
   - Input: push-to-talk on the spacebar always works. Optional offline wake word.
   - Fast path: keyword/pattern match for about 12 known commands. No LLM.
   - Smart path: Groq function calling.
   - Knowledge path: small RAG over SOP and contacts documents.
   - A pre-recorded acknowledgement plays immediately while the action runs.
   - A spoken "yes" only confirms launching a big action (`/assistant/confirm`). Big actions are: call everyone, evacuate, call the fire station. Calling one person or one zone is not gated.
   - The assistant can acknowledge any incident. It cannot close a critical incident; that needs the dashboard.
   - Commands: call everybody/person/zone; broadcast a message; status report; show camera N; what happened in the last hour; acknowledge or resolve an incident; stop all calls; who has not responded; call the fire station (only when a human orders it); SOP questions.
6. **Audit log.** Every action records who, what, when, and channel (dashboard, phone or voice). Visible to Admin and Manager.
7. **Heartbeat.** If a camera or worker goes down for 30 s, WhatsApp the Admin.
8. **Cloud and training decisions (18–19 Oct, confirmed by Kirito).** Sending short text alerts through cloud services (Twilio, WhatsApp, Groq) is acceptable under the on-premise rule. Video never leaves the laptop. Organisers allow demo access. If that fails at the venue, we switch to the alternatives in the roadmap's risk table. Google Colab is the fallback for training custom PPE and fire/smoke models. Each Colab use gets a line in `docs/disclosures.md`.

Build priority if time runs short: detection, rules and dashboard → roles and delegation → severity routing and escalation → phone commands and roll call → laptop assistant → RAG and wake word.

Frontend: all of the above is confirmed in `docs/CONTRACT.md`. The teammate updates `frontend/BRIEF.md` to point at the contract.

## Commands (PowerShell, from backend/)
```
py -3.11 -m venv .venv ; .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000
python -m worker.run_worker --camera cam-webcam
pytest
yolo export model=models/yolo11n-pose.pt format=openvino imgsz=480
yolo export model=models/yolo11n.pt format=openvino imgsz=480
ngrok http 8000
```

## .env keys
DATABASE_URL, INTERNAL_KEY, PUBLIC_BASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, WHATSAPP_API_URL, WHATSAPP_TOKEN, GROQ_API_KEY, GROQ_MODEL, GROQ_STT_MODEL, OLLAMA_URL, OLLAMA_MODEL, OFFLINE_MODE, MEDIA_DIR, STREAM_SECRET, DEMO_USER_PINS, DEMO_RESPONDER_PHONES, FIRE_STATION_NUMBER, SITE_ADDRESS, PPE_MODEL_PATH, FIRE_MODEL_PATH, CALLS_MODE, WHATSAPP_MODE

## Speed targets
Detection 8+ analysed FPS per camera (2 cameras) · incident to dashboard < 2 s · incident to phone ringing < 15 s · typed answer about 2 s online · spoken answer about 4 s online · offline answer < 8 s.

## AI assistant
- LLM output is ONLY a JSON filter object, validated by Pydantic: `{types[], cameras[], severities[], statuses[], from, to, limit}`. Our code builds the SQLAlchemy query. The LLM never writes SQL.
- Groq with a 5 s timeout; on failure or offline mode, fall back to Ollama automatically. Model names come from `.env`.
- Short answer text is generated from the returned rows (count + summary), not invented.
- Voice: Groq Whisper online, faster-whisper "small" offline.
- Log milliseconds for every step.

## Reliability
- Timeouts (5 s) + one retry on every Twilio, WhatsApp and Groq call. If Twilio fails, the dashboard and WhatsApp still alert.
- Structured logs (incident id, step, ms).
- Tests: rules engine (fake detections in, expected incidents out) and the assistant filter parser.

## Current status (update this daily)
- Day: Wed 21 Oct, full backend (everything that does not need Kirito's keys or decisions)
- Done: 19 Oct: latest-frame capture thread, yolo11n-pose tracking with torso angle, MJPEG per camera. 20 Oct (Gate 1): zones in the database (`config/zones.yaml` + `python -m app.seed`); rules engine with access, loitering, crowding, fall, unattended bag, 60 s cooldown, night window; ring buffer + 10 s H.264 clip via FFmpeg; snapshot; incident stored and the API told via `/internal/incidents/{id}/created`, which broadcasts on the WebSocket manager. Gate 1 check passed on a test video: access incident in the database with snapshot and clip. pytest: 33 passed, using a separate test database.
- Done (Gate 2): PIN login with opaque tokens (hashed, 12 h) and a 5-per-minute limit; 4 demo users from `config/demo_users.yaml`; `/me`, `/users`, `/cameras` with signed stream URLs, `/stream` proxy, `/zones`, `/events`, ack (idempotent) and resolve, `/stats`, `/audit-log`, `/responders`; `/ws/events?token=` filtered per user; audit row on every action; worker heartbeats make `/health` and `/cameras` live; OpenVINO exports used automatically (pose model 49 -> 25 ms per frame on the sandbox CPU). Live check on a real server passed: Guard got the incident on the WebSocket, saw only cam-webcam, streamed it through the proxy, acknowledged it, and the update arrived live. pytest: 55 passed.
- Done (21 Oct, full backend): escalation ladders on timers that stop at the first acknowledgement; Twilio calls through a 1-per-second queue with signed webhooks (keypad + speech: 1 ack, 2 false alarm for high only, 3 supervisor, 9 repeat, "alert everybody" with confirm); WhatsApp alerts with a signed snapshot link; low-severity repeat call; 18:00 digest; camera heartbeat watchdog; alert everybody + roll call board; broadcast; assistant query (Groq -> Ollama -> keyword parser), voice (Groq Whisper -> faster-whisper), 13 commands with a spoken-yes step for big actions, SOP answers from `knowledge/`; PPE and fire rules (off until a model is chosen); public-address guard so ngrok only exposes the webhooks. Live run in simulate mode passed end to end. pytest: 135 passed.
- Done (22 Oct): 3-camera demo layout (phone 1 restricted door, phone 2 shop floor for fall/bag/fire, laptop 2 entrance for crowding/PPE); `tools/camera_share.py`; seed removes zones dropped from zones.yaml; tests use frozen config fixtures; `docs/DEMO_SETUP.md`. End-to-end check: camera_share -> worker -> /health online at 5 analysed fps from a 15 fps source, so sources should send 25+ fps. pytest: 140 passed.
- Done (25 Oct): invitations by email with link and QR (P19); sector summaries (P20); sites, buildings, areas and camera placement; guard profiles with encrypted personal details (admin only), replace-on-save, field-only audit; badge scans; presence; "no badge scanned" on restricted entries (alert and summary); manager delegation of cameras and actions (P21); Docker PostgreSQL file. Not yet built: wake word and voice navigation, dataset training, phone automation for area alerts.
- Done (26 Oct, voice Step 1): wake-word listener with openWakeWord (`backend/voice/wake.py`, `python -m voice.run_listener`): trigger rules with a cooldown, plays `media/ack/yes_boss.wav`, settings WAKE_*. Tests use fake scores; the real microphone test is still to be done on the laptop.
- Done (26 Oct, P22–P24): cameras added, tested, numbered and started from the API (workers read cameras from the database; `WORKERS_AUTOSTART` restarts crashed ones); role templates (`/roles`) with cameras and actions; staff IDs in invitations; duty check-in by QR pass or post QR, off-duty guards not called, shift-roster fallback with `duty_fallback` + Head Guard WhatsApp; `/overview`, `/activity` (AI questions/answers, camera views; Admin and Manager only), `/usage`; voice summaries (today, past hour, cameras 6, 7 and 8), "who is on duty", "show cameras 3, 4 and 12"; Rumik Silk voice (`/assistant/speak`, `python -m tools.check_rumik`). Live check: a camera added by API started its worker, came online at 8.3 analysed fps, streamed, and stopped with the API. pytest: 200 passed (the real wake-word model test needs tflite, which this sandbox's Python cannot install).
- Done (26 Oct, P25 part 1): calls depend on who is on the software (away = at once, on screen = 5 s grace, acknowledging cancels the call); "Demo alert" button with a live timeline panel; Live page alerts once per incident (it beeped on every update). Browser test: Ravi called at 0 s, Anita and Vikram at 5 s; Anita acknowledging at 2 s cancelled her call; panel opens in about 0.2 s. pytest: 210 passed.
- Done (26 Oct, admin screens): phone required when adding or inviting a person, on the call list at once (P26); animated sidebar (Monitor, Team, System); Dashboard (bento: cameras, problems now, 7 days, guards on/off duty, today, 30-day trend, data usage); People and roles (add person with invitation QR, edit phone/zone/shift/cameras, roles with cameras and actions); Cameras (test, add and start, start/stop/restart, disable, worker log); Guards and duty (board, check in/out, QR pass, history); Logs (per person, Friday questions and answers, camera views, all actions); Incident detail (clip, snapshot, timeline, ack/resolve). All times shown in IST. Browser check: every screen loads with no console errors. pytest: 214 passed.
- Done (26 Oct, Friday): Friday's page with a blue three.js blob that moves with her voice (and yours while you talk), text below it, hold Space or the mic, typed orders, Yes/No for big actions; alarm red glow + spoken alarm once per incident; `POST /assistant/voice-command`; wake-word listener loop (`voice/session.py`, `voice/api_client.py`): "Hey Friday" -> "Yes, boss" -> opens the browser on Friday's page when the software is closed, else the app jumps there -> records until 1 s of quiet -> answer spoken on FRIDAY_USER's dashboard or the laptop speaker; spoken yes for big actions (anything else cancels). Browser checks: page jump 25 ms, alarm glow clears on acknowledge. pytest: 230 passed. Not yet tested with a real microphone, Rumik key or Groq speech to text (all on Kirito's laptop).
- Done (26 Oct, wake word kit): `backend/tools/wake_training/README.md` (Colab, openWakeWord automatic training, "hey friday" settings, download, tune the threshold); `WAKE_MODEL` accepts a trained file path (`models/hey_friday.onnx`), never downloads it, and says where it looked when it is missing.
- Next (Kirito): fill `.env` (Twilio, WhatsApp, Groq, Ollama, phones, fire station, site address), port Bhoomi's WhatsApp format if different, choose PPE/fire models, real SOPs in `knowledge/`, sign off contract P1-P18, then the full testing checklist on the laptop.
- Blockers: needs Kirito's keys and decisions above. Offline speech model download (Hugging Face) could not be tested in the sandbox. Not tested with a real webcam or phone camera.
