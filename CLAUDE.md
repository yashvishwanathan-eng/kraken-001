# CLAUDE.md: SafetyAI backend

## Project
Hackathon build: "AI Video Intelligence and Workplace Safety System" (Sustainathon, VIT Bengaluru, event 26 Oct 2026; everything finished by 25 Oct). Product name is a placeholder ("SafetyAI"), kept in ONE config constant.
An on-premise system that watches CCTV cameras, turns detections into confirmed incidents, saves a clip and snapshot per incident, phones the right security guard with an escalation ladder, and answers typed or spoken questions about incidents.
Pitch: "Every factory has cameras nobody watches. Most AI adds a dashboard nobody watches. Ours picks up the phone."

## Team and boundaries
- I (Kirito) own `backend/`: vision, rules engine, API, database, calls, AI assistant.
- My teammate owns `frontend/` (React, built with Antigravity, brief in `frontend/BRIEF.md`).
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

## Stack
Python 3.11 · FastAPI · Uvicorn · SQLAlchemy 2 · Alembic · PostgreSQL (local) · APScheduler · httpx
Vision: Ultralytics YOLO (`yolo11n.pt`, `yolo11n-pose.pt`, built-in ByteTrack) · OpenCV · OpenVINO export · FFmpeg
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
    routers/           health, cameras, stream, events, zones, responders, broadcast, assistant, stats, twilio, internal, auth, users, roll_call, audit
    services/          escalation.py, calls.py, whatsapp.py, assistant.py, llm.py, stt.py, clips.py, permissions.py, audit.py
  worker/
    run_worker.py      one process per camera: python -m worker.run_worker --camera cam-east-wing
    detectors.py       detect, track, pose, PPE, fire models
    rules.py           all incident rules (pure functions + small state, unit-testable)
    buffer.py          ring buffer + clip writer
    mjpeg.py           annotated MJPEG server on 127.0.0.1:81xx
  config/cameras.yaml  cameras: id, name, source (0, http://PHONE-IP:8080/video, file path, rtsp://), port
  alembic/
  tests/
  models/              model weights (gitignored)
  media/               clips and snapshots (gitignored)
docs/
  CONTRACT.md          API contract: endpoints, shapes, incident object, enums, auth rules
  disclosures.md
```

## How the parts talk
- Each camera runs its own worker process. The API never runs detection inside a request.
- Worker serves annotated MJPEG on its own port; `GET /stream/{camera_id}` proxies it (httpx streaming).
- Worker confirms an incident, saves clip + snapshot, inserts the row, then calls `POST /internal/incidents/{id}/created` on the API (header `X-Internal-Key`). The API broadcasts it on `/ws/events` and starts escalation.
- Worker sends `POST /internal/heartbeat` (proposed). If a camera goes silent for 30 s, the API WhatsApps the Admin.
- Worker reconnects to a lost camera every 5 s and reports status; `/health` shows each camera online/offline + FPS.
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
- `LOW_REPEAT_COUNT=3`, `LOW_REPEAT_WINDOW_MIN=60`.
- `CRITICAL_ADMIN_DELAY_S=60` (critical: admin is called after the head guard has had 60 s).

## Escalation and calls (resolved 18 Oct)
Times are cumulative from incident creation. A ladder stops at the first acknowledgement, from any channel.

Routing by severity:
- **Critical** (fire, fall; restricted entry at night):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin (snapshot).
  - 60 s: call the Admin.
  - 2 min: call the Manager.
  - Never call above Admin.
- **High** (restricted entry in the day, unattended bag, crowding):
  - 0 s: call the Head Guard of that camera's group. WhatsApp the Admin.
  - 60 s: call the Manager.
  - The Admin gets WhatsApp only, not a call.
- **Low** (ppe, loitering):
  - No calls.
  - WhatsApp to the zone's Manager (the Manager role delegated to that zone).
  - Daily digest at 18:00 IST.
  - If the same zone repeats 3 times within 1 hour, call the Head Guard once for that zone.

Call mechanics:
- Alert audio is generated BEFORE dialling; the call opens with `<Play>` immediately.
- `<Gather input="speech dtmf">`: 1 = going (ack), 2 = false alarm, 3 = connect supervisor, 9 = repeat. Speech mapped to the same 4 intents (keyword match first, short Groq prompt as fallback).
- Phone acknowledgements (keypad or voice) use the responder linked to the call. They never take the user from the request body.
- **Critical**: voice and keypad can only ACKNOWLEDGE. Closing needs the dashboard.
- **High**: the guard may mark false alarm (logged with name).
- **Low**: the guard may resolve from the dashboard.
- Webhooks are idempotent (a repeated keypress never double-acknowledges). Every step is written to the timeline, the audit log and `/ws/events`.
- Head Guard and Admin can speak or press keys during an alert call.
- Never auto-call fire services or 112.
- Twilio trial: only verified numbers can be called.

## Product updates (18 Oct)
Decisions from the 18 Oct product review. Escalation details are in the section above; the endpoint list is in `docs/CONTRACT.md`.

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
   - A spoken "yes" only confirms launching a big action (`/assistant/confirm`). Big actions are: call everyone, evacuate, call the fire station.
   - The assistant can acknowledge any incident. It cannot close a critical incident; that needs the dashboard.
   - Commands: call everybody/person/zone; broadcast a message; status report; show camera N; what happened in the last hour; acknowledge or resolve an incident; stop all calls; who has not responded; call the fire station (only when a human orders it); SOP questions.
6. **Audit log.** Every action records who, what, when, and channel (dashboard, phone or voice). Visible to Admin and Manager.
7. **Heartbeat.** If a camera or worker goes down for 30 s, WhatsApp the Admin.

Build priority if time runs short: detection, rules and dashboard → roles and delegation → severity routing and escalation → phone commands and roll call → laptop assistant → RAG and wake word.

Frontend: the proposed endpoints, the `POST /events/{id}/ack` change and the proposed timeline actions are in `docs/CONTRACT.md` and still need the teammate's sign-off. The teammate updates `frontend/BRIEF.md` to point at the contract.

## Commands (PowerShell, from backend/)
```
py -3.11 -m venv .venv ; .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
python -m worker.run_worker --camera cam-webcam
pytest
yolo export model=models/yolo11n.pt format=openvino
ngrok http 8000
```

## .env keys
DATABASE_URL, INTERNAL_KEY, PUBLIC_BASE_URL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, WHATSAPP_API_URL, WHATSAPP_TOKEN, GROQ_API_KEY, GROQ_MODEL, GROQ_STT_MODEL, OLLAMA_URL, OLLAMA_MODEL, OFFLINE_MODE, MEDIA_DIR

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
- Day: Sun 18 Oct, setup and skeleton
- Done: CLAUDE.md saved; backend skeleton (FastAPI app, config, CORS, /health stub, one test, .env.example); docs/disclosures.md started. pytest passes (1 test); API boots and /health returns the contract shape. Product decisions resolved; API contract moved to docs/CONTRACT.md.
- Next: database models and Alembic setup; cameras and zones endpoints.
- Blockers: none
