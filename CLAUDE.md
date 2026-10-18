# CLAUDE.md: SafetyAI backend

## Project
Hackathon build: "AI Video Intelligence and Workplace Safety System" (Sustainathon, VIT Bengaluru, event 26 Oct 2026; everything finished by 25 Oct). Product name is a placeholder ("SafetyAI"), kept in ONE config constant.
An on-premise system that watches CCTV cameras, turns detections into confirmed incidents, saves a clip and snapshot per incident, phones the right security guard with an escalation ladder, and answers typed or spoken questions about incidents.
Pitch: "Every factory has cameras nobody watches. Most AI adds a dashboard nobody watches. Ours picks up the phone."

## Team and boundaries
- I (Kirito) own `backend/`: vision, rules engine, API, database, calls, AI assistant.
- My teammate owns `frontend/` (React, built with Antigravity, brief in `frontend/BRIEF.md`).
- NEVER create, edit or delete anything in `frontend/`.
- The API contract below is shared with the frontend. Never change it silently. If a change is needed, stop and tell me, so I can update `frontend/BRIEF.md` too.

## Working rules
1. Before writing code, read the relevant existing files. Never guess field names, paths or endpoints.
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
    schemas.py         Pydantic models (mirror the contract exactly)
    ws.py              WebSocket connection manager
    routers/           health, cameras, stream, events, zones, responders, broadcast, assistant, stats, twilio, internal
    services/          escalation.py, calls.py, whatsapp.py, assistant.py, llm.py, stt.py, clips.py
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
docs/disclosures.md
```

## How the parts talk
- Each camera runs its own worker process. The API never runs detection inside a request.
- Worker serves annotated MJPEG on its own port; `GET /stream/{camera_id}` proxies it (httpx streaming).
- Worker confirms an incident, saves clip + snapshot, inserts the row, then calls `POST /internal/incidents/{id}/created` on the API (header `X-Internal-Key`). The API broadcasts it on `/ws/events` and starts escalation.
- Worker reconnects to a lost camera every 5 s and reports status; `/health` shows each camera online/offline + FPS.
- Models load once at startup and run one warm-up frame.

## API contract (source of truth, identical to frontend/BRIEF.md)
| Method | Path | Notes |
|---|---|---|
| GET | /health | `{cameras:[{id,online,fps}], db, twilio, llm}` |
| GET | /cameras | `[{id,name,stream_url,online,fps,zones:[{id,name,kind,polygon:[[x,y],...]}]}]`, kind = restricted, ppe, work |
| GET | /stream/{camera_id} | MJPEG (`multipart/x-mixed-replace`) |
| GET | /events | filters: type, camera, severity, status, from, to (ISO UTC); newest first |
| GET | /events/{id} | incident with timeline |
| POST | /events/{id}/ack | body `{by}` |
| POST | /events/{id}/resolve | body `{outcome: "resolved" or "false_alarm", note}` |
| WS | /ws/events | sends full incident objects on create and update |
| GET, POST, PUT | /zones | polygons per camera |
| GET, POST, PUT, DELETE | /responders | `{id,name,phone,language,zone,shift_start,shift_end,call_order,active}` |
| POST | /broadcast | multipart: zone + audio file |
| POST | /assistant/query | `{question}` returns `{answer, incidents[], source:"groq" or "ollama", ms}` |
| POST | /assistant/voice | multipart audio returns `{transcript, answer, incidents[], source, ms}` |
| GET | /stats | `{by_type:{...}, by_day:[{date,count}], open, resolved, avg_response_s}` |
| POST | /twilio/voice, /twilio/gather, /twilio/status | Twilio webhooks only |
| POST | /internal/incidents/{id}/created | worker to API only |

Incident object:
```json
{
  "id": 128, "type": "fire", "severity": "critical", "status": "acknowledged",
  "camera_id": "cam-east-wing", "camera_name": "East Wing, Loading Bay 2",
  "zone": "loading-bay", "started_at": "2026-10-24T10:12:41Z", "confidence": 0.87,
  "snapshot_url": "/media/128.jpg", "clip_url": "/media/128.mp4",
  "acknowledged_by": "Ravi",
  "timeline": [
    {"at": "2026-10-24T10:12:44Z", "action": "call_placed", "to": "Ravi"},
    {"at": "2026-10-24T10:12:58Z", "action": "acknowledged", "by": "Ravi", "via": "keypad"}
  ]
}
```
type: access, ppe, fall, fire, unattended, loitering, crowding · severity: critical, high, low · status: open, acknowledged, resolved, false_alarm · timeline actions: detected, call_placed, call_no_answer, acknowledged, escalated, whatsapp_sent, resolved, false_alarm. All times stored and returned in UTC.
DB indexes on incidents: started_at, type, camera_id, status.

## Incident rules (rules.py; thresholds in config, not hard-coded)
| Type | Severity | Fires when |
|---|---|---|
| access | high | person's FEET point (bottom-centre of box) inside a restricted zone for 3 s |
| ppe | low | person in a ppe zone without helmet or vest in 10 consecutive analysed frames |
| fall | critical | pose torso near-horizontal + hips dropped, and stays down 5 s (bending is not a fall) |
| fire | critical | fire or smoke in 5 consecutive analysed frames, confidence ≥ 0.5 |
| unattended | high | backpack/handbag/suitcase still for 60 s with no person within reach |
| loitering | low | same track ID in one zone for 120 s |
| crowding | high | more than N people in a zone (N per zone) |
- Detection confidence threshold 0.5. Cooldown: same camera + type cannot fire again within 60 s.
- Analyse every 3rd frame, `imgsz=480`, OpenVINO models when exported.
- Clip: 5 s before + 5 s after from the ring buffer, re-encoded with FFmpeg `-c:v libx264 -pix_fmt yuv420p -movflags +faststart` so browsers play it.

## Escalation and calls
- Ladder: 0 s nearest on-shift guard for that zone (+ WhatsApp snapshot) → 60 s next guard → 2 min supervisor → 5 min manager. Stops at acknowledgement.
- Alert audio is generated BEFORE dialling; the call opens with `<Play>` immediately.
- `<Gather input="speech dtmf">`: 1 = going (ack), 2 = false alarm, 3 = connect supervisor, 9 = repeat. Speech mapped to the same 4 intents (keyword match first, short Groq prompt as fallback).
- Critical (fire, fall): voice/keypad can only acknowledge; closing needs the dashboard. High: guard may mark false alarm (logged with name). Low: guard may resolve.
- Webhooks are idempotent (a repeated keypress never double-acknowledges). Every step is written to the timeline and pushed on /ws/events.
- Never auto-call fire services or 112.
- Twilio trial: only verified numbers can be called.

## AI assistant
- LLM output is ONLY a JSON filter object, validated by Pydantic: `{types[], cameras[], severities[], statuses[], from, to, limit}`. Our code builds the SQLAlchemy query. The LLM never writes SQL.
- Groq with a 5 s timeout; on failure or offline mode, fall back to Ollama automatically. Model names come from `.env`.
- Short answer text is generated from the returned rows (count + summary), not invented.
- Voice: Groq Whisper online, faster-whisper "small" offline.
- Log milliseconds for every step.

## Speed targets
Detection 8+ analysed FPS per camera (2 cameras) · incident to dashboard < 2 s · incident to phone ringing < 15 s · typed answer about 2 s online · spoken answer about 4 s online · offline answer < 8 s.

## Reliability
- Timeouts (5 s) + one retry on every Twilio, WhatsApp and Groq call. If Twilio fails, the dashboard and WhatsApp still alert.
- Structured logs (incident id, step, ms).
- Tests: rules engine (fake detections in, expected incidents out) and the assistant filter parser.

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

## Current status (update this daily)
- Day: Sun 18 Oct, setup and skeleton
- Done: CLAUDE.md saved; backend skeleton (FastAPI app, config, CORS, /health stub, one test, .env.example); docs/disclosures.md started. pytest passes (1 test); API boots and /health returns the contract shape.
- Next: database models and Alembic setup; cameras and zones endpoints.
- Blockers: none
