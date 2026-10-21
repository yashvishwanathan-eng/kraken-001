# CLAUDE.md: SafetyAI backend

## Project
Hackathon build: "AI Video Intelligence and Workplace Safety System" (Sustainathon, VIT Bengaluru, event 26 Oct 2026; everything finished by 25 Oct). Product name is a placeholder ("SafetyAI"), kept in ONE config constant.
An on-premise system that watches CCTV cameras, turns detections into confirmed incidents, saves a clip and snapshot per incident, phones the right security guard with an escalation ladder, and answers typed or spoken questions about incidents.
Pitch: "Every factory has cameras nobody watches. Most AI adds a dashboard nobody watches. Ours picks up the phone."

## Team and boundaries
- I (Kirito) build the full `backend/`: vision, rules engine, API, database, calls, AI assistant.
- My teammate builds the full `frontend/` (React, built with Antigravity, brief in `frontend/BRIEF.md`). The frontend is combined with the backend last.
- NEVER create, edit or delete anything in `frontend/`.
- The API contract is `docs/CONTRACT.md`. It is the single source of truth, shared with the frontend. Never change it silently. If a change is needed, stop and tell me, so the teammate can update `frontend/BRIEF.md` (which points to the contract, not a copy).

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
10. Branches: day work goes on `claude/festive-clarke-6w890k`. Do not use `main` for day work. Commit locally; ask Kirito before every push and push only on a yes.

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
    routers/           health, cameras, stream, events, zones, responders, broadcast, assistant, stats, twilio, internal, auth, users, roll_call (/alerts/all + /roll-call), audit, share (signed media for WhatsApp/Twilio)
    services/          escalation.py (ladders, digest, heartbeat watchdog), calls.py (Twilio + 1/s queue), whatsapp.py, alerts.py (alert everybody, roll call),
                       incidents.py (ack/close shared by dashboard, phone, assistant), directory.py (who to call), assistant.py (question -> filter -> answer),
                       commands.py (laptop commands + confirm), knowledge.py (SOP answers), llm.py (Groq -> Ollama), stt.py (Groq Whisper -> faster-whisper),
                       permissions.py (PINs, sessions, camera scope, signed links), audit.py, camera_status.py (heartbeats)
  worker/
    run_worker.py      one process per camera: python -m worker.run_worker --camera cam-east-wing
    capture.py         latest-frame capture thread: reads the camera nonstop, keeps only the newest frame and the newest every-Nth keyframe, reconnects every 5 s
    detectors.py       person detection, tracking and pose (yolo11n-pose), torso angle; PPE and fire models later
    rules.py           all incident rules (pure functions + small state, unit-testable)
    buffer.py          ring buffer + clip writer
    incidents.py       stores a confirmed incident (row, timeline, snapshot), notifies the API, writes the clip 5 s later
    heartbeat.py       posts online + analysed FPS to /internal/heartbeat every 5 s
    mjpeg.py           annotated MJPEG server on 127.0.0.1:81xx
  config/cameras.yaml  cameras: id, name, source (0, http://PHONE-IP:8080/video, file path, rtsp://), port
  config/zones.yaml    demo zones per camera; polygon points are fractions of the frame (0..1); max_people for crowding
  config/demo_users.yaml  the 4 demo users (role, cameras, actions); PINs come from DEMO_USER_PINS in .env
  config/demo_responders.yaml  call roster (zone, shift, call order); phones come from DEMO_RESPONDER_PHONES in .env
  knowledge/           SOP and contact documents (.md/.txt) the assistant answers from; SAMPLE-sop.md is invented demo content
  alembic/
  tests/               conftest.py points tests at a separate `<db>_test` database
  models/              model weights + OpenVINO exports (gitignored); workers use <name>_openvino_model/ when present
  media/               clips and snapshots (gitignored)
docs/
  CONTRACT.md          API contract: endpoints, shapes, incident object, enums, auth rules
  GUIDE.md             start here: every topic from zero (simple, deeper, where, try it, check yourself) + first full run
  STUDY.md             Kirito's study notes: every concept with a tiny example, updated as we build
  READ_FIRST.md        the simple, non-technical explanation of the whole system (read before anything else)
  TECHNICAL.md         the full technical reference: every module, flow, setting, test, run and fix
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
- `TWILIO_CPS=1` (new calls per second), `CALL_RING_TIMEOUT_S=30`, `CALL_LANGUAGE=en-IN`. Ladder delays: `CRITICAL_ADMIN_DELAY_S=60`, `CRITICAL_MANAGER_DELAY_S=120`, `HIGH_MANAGER_DELAY_S=60`.
- Role labels the ladder looks for: `ROLE_ADMIN=Admin`, `ROLE_MANAGER=Manager`, `ROLE_HEAD_GUARD=Head Guard`, `ROLE_GUARD=Guard`.
- `LLM_TIMEOUT_S=5` (Groq), `OLLAMA_TIMEOUT_S=10`, `ASSISTANT_CONFIRM_TTL_S=60`, `KNOWLEDGE_DIR=knowledge`.
- PPE/fire labels: `PPE_HELMET_LABELS`, `PPE_VEST_LABELS`, `FIRE_LABELS` (class names differ between models), `PPE_MISSING_FRAMES=10`, `FIRE_FRAMES=5`.
- `LOW_REPEAT_COUNT=3`, `LOW_REPEAT_WINDOW_MIN=60`.
- `CRITICAL_ADMIN_DELAY_S=60` (critical: admin is called after the head guard has had 60 s).

## Escalation and calls (confirmed 18 Oct)
Times are cumulative from incident creation. A ladder stops at the first acknowledgement, from any channel.

Routing by severity:
- **Critical** (fire, fall; restricted entry at night):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin (snapshot).
  - 60 s: call the Admin.
  - 2 min: call the Manager (fallback if the Admin does not answer). Confirmed.
  - Never call above Admin.
- **High** (restricted entry in the day, unattended bag, crowding):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin.
  - 60 s: call the Manager.
  - The Admin gets WhatsApp only, not a call.
- **Low** (ppe, loitering):
  - No calls.
  - WhatsApp to the zone manager: the Manager role delegated to that zone. Confirmed.
  - Daily digest at 18:00 IST.
  - If the same zone repeats 3 times within 1 hour, call the Head Guard once for that zone.

Call mechanics:
- Alert text is ready BEFORE dialling. Calls open in English with `<Say>` straight away. Tamil and Hindi are not used at the start of a call.
- `<Gather input="speech dtmf">`: 1 = going (ack), 2 = false alarm, 3 = connect supervisor, 9 = repeat. Speech mapped to the same 4 intents (keyword match first, short Groq prompt as fallback).
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
5. **Laptop assistant.** Internal nickname "Jarvis". Public and UI name is the product name (`PRODUCT_NAME`). Jarvis must not appear in user-facing text.
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
- Next (Kirito): fill `.env` (Twilio, WhatsApp, Groq, Ollama, phones, fire station, site address), port Bhoomi's WhatsApp format if different, choose PPE/fire models, real SOPs in `knowledge/`, sign off contract P1-P18, then the full testing checklist on the laptop.
- Blockers: needs Kirito's keys and decisions above. Offline speech model download (Hugging Face) could not be tested in the sandbox. Not tested with a real webcam or phone camera.
