# SafetyAI backend: technical reference

Read `docs/READ_FIRST.md` first. That file explains the ideas in plain words. This one is the full engineering reference for when you start running, testing and changing things.

- The API shapes live in `docs/CONTRACT.md`, which is the single source of truth. This file only points to them.
- Concepts from zero are in `docs/GUIDE.md`. Code-level study notes are in `docs/STUDY.md`.
- All commands are PowerShell, run from `backend/` unless a section says otherwise.

---

## 1. Architecture: what runs where

The backend runs as one API process, plus one worker process per camera.

```
   camera (webcam / phone / file / rtsp)
          |
   +------v------------------------------------------------+
   | WORKER process  (python -m worker.run_worker --camera X) |
   |  capture thread  -> newest frame + every-3rd keyframe   |
   |  main loop       -> YOLO pose+track, bags, (ppe, fire)  |
   |                  -> rules engine -> confirmed incident  |
   |  ring buffer     -> 10 s clip (FFmpeg H.264) + snapshot |
   |  MJPEG thread    -> 127.0.0.1:81xx (annotated video)    |
   |  heartbeat thread-> POST /internal/heartbeat every 5 s  |
   +------|-----------------------------------|--------------+
          | inserts incident row (DB)         | POST /internal/incidents/{id}/created
          v                                   v
   +-------------------------------------------------------------+
   | API process  (uvicorn app.main:app --port 8000)              |
   |  FastAPI routers (REST + /ws/events WebSocket)               |
   |  escalation scheduler (APScheduler thread): ladders, digest, |
   |      heartbeat watchdog                                      |
   |  call dispatcher thread: 1 new Twilio call per second        |
   |  WhatsApp sender pool (8 threads)                            |
   +-----|---------------|----------------|-----------------------+
         |               |                |
     PostgreSQL     Twilio / WhatsApp   Groq -> Ollama -> keywords
                    (via ngrok webhook)
```

Rules that keep this design simple and fast:
- **The API never runs detection.** Each worker owns its camera. If one crashes, the API and the other cameras keep running.
- **Database first, then a notice.** The worker writes the incident row itself, then calls `/internal/incidents/{id}/created`. The API reloads the row, broadcasts it on the WebSocket and starts the ladder. If the notice fails, the incident is still saved.
- **Timers live in the API.** Ladder steps are APScheduler "date" jobs named `ladder:{incident_id}:{step}`. An acknowledgement removes every remaining job for that incident.
- **Calls go through a queue.** `CallService` has one dispatcher thread that places at most `TWILIO_CPS` (default 1) new calls per second. Twilio trial accounts reject faster dialling.
- **Pushing to the WebSocket from other threads.** The lifespan stores the event loop in `manager.loop`. Timer and dispatcher threads then call `publish_from_thread()`, which uses `asyncio.run_coroutine_threadsafe`.

## 2. Repository map (backend)

| Path | What it does |
|---|---|
| `app/main.py` | App, CORS, lifespan (starts the escalation scheduler and the call dispatcher), public-address guard middleware, generic 500 handler, `/media` static files, all routers |
| `app/config.py` | `Settings` (pydantic-settings, reads `.env`), `PRODUCT_NAME`, `media_root()`, camera YAML loader |
| `app/db.py` | Engine and `get_db` session dependency |
| `app/models.py` | SQLAlchemy tables (section 3) |
| `app/schemas.py` | Pydantic models that mirror `docs/CONTRACT.md` |
| `app/ws.py` | WebSocket manager: per-user camera filter, `publish`, `publish_from_thread` |
| `app/deps.py` | `current_user` (401) and `require(action)` (403) |
| `app/seed.py` | `python -m app.seed`: cameras, zones, demo users, demo responders. Safe to rerun |
| `app/routers/health.py` | `GET /health`: database, cameras (live heartbeats), twilio, llm |
| `app/routers/auth.py` | `POST /auth/pin`, `GET /me` |
| `app/routers/users.py` | `/users` CRUD (Admin) |
| `app/routers/cameras.py` | `/cameras` with signed `stream_url`, `/{id}/stream-url` |
| `app/routers/stream.py` | `GET /stream/{camera_id}?st=` proxies the worker's MJPEG |
| `app/routers/zones.py` | `/zones` list and edit |
| `app/routers/events.py` | `/events` list/detail, ack, resolve, the `/ws/events` WebSocket |
| `app/routers/stats.py` | `/stats` |
| `app/routers/audit.py` | `/audit-log` (Admin, Manager) |
| `app/routers/responders.py` | `/responders` CRUD |
| `app/routers/roll_call.py` | `POST /alerts/all` ("alert everybody"), `GET /roll-call` |
| `app/routers/broadcast.py` | `POST /broadcast` (upload an MP3/WAV, call a zone, play it) |
| `app/routers/assistant.py` | `/assistant/query`, `/voice`, `/command`, `/confirm` |
| `app/routers/twilio.py` | Twilio webhooks `/twilio/voice`, `/gather`, `/status`; TwiML; keypad and speech handling |
| `app/routers/share.py` | `GET /share/media/{name}?st=`: signed snapshot links for WhatsApp |
| `app/routers/internal.py` | Worker-only: `/internal/incidents/{id}/created`, `/internal/heartbeat` (header `X-Internal-Key`) |
| `app/services/permissions.py` | PIN hashing, sessions, camera scope, signed stream and share tokens |
| `app/services/audit.py` | `record(...)` writes an audit row in the caller's transaction |
| `app/services/camera_status.py` | In-memory heartbeat table: online, FPS, last seen |
| `app/services/incidents.py` | Shared incident operations: `load`, `add_timeline`, `acknowledge` (idempotent), `close`, `to_message` |
| `app/services/directory.py` | Who to call: `on_shift`, `contacts_for_role`, `on_shift_contacts`, `contact_by_name` |
| `app/services/escalation.py` | Ladders, `alert_text`, low-repeat rule, 18:00 digest, heartbeat watchdog |
| `app/services/calls.py` | `TwilioClient` (REST over httpx, retry rules) and `CallService` (queue, modes, hang-up) |
| `app/services/whatsapp.py` | `WhatsAppService` (Meta Cloud API format, PORT FROM BHOOMI), simulate list |
| `app/services/alerts.py` | `alert_all(...)`, roll-call summary and notices |
| `app/services/llm.py` | Groq then Ollama: `chat`, `chat_json`, `choose_tool`, `classify_intent`, `status` |
| `app/services/stt.py` | Speech to text: Groq Whisper, then local faster-whisper |
| `app/services/assistant.py` | Question to `IncidentFilter` to rows to answer text |
| `app/services/commands.py` | Spoken/typed commands: regex fast path, then Groq tools, then knowledge; confirm tokens for big actions |
| `app/services/knowledge.py` | Small RAG: TF-IDF over `knowledge/*.md|txt` |
| `app/services/clips.py` | Media path helpers |
| `worker/run_worker.py` | Worker entry point and main loop |
| `worker/capture.py` | Latest-frame capture thread, reconnect every 5 s |
| `worker/detectors.py` | Pose and track (yolo11n-pose), bag detector, optional PPE/fire `ClassDetector`, OpenVINO selection |
| `worker/rules.py` | Rules engine: access, loitering, crowding, fall, unattended, ppe, fire; cooldown; night window |
| `worker/buffer.py` | Ring buffer and FFmpeg clip writer |
| `worker/incidents.py` | Saves the row, timeline and snapshot, notifies the API, writes the clip 5 s later |
| `worker/heartbeat.py` | Posts online + FPS every 5 s |
| `worker/mjpeg.py` | Annotated MJPEG server on `127.0.0.1:81xx` |
| `config/cameras.yaml`, `zones.yaml`, `demo_users.yaml`, `demo_responders.yaml` | Demo data loaded by the seed: 3 stage cameras (`cam-door`, `cam-floor`, `cam-entrance`) plus `cam-webcam` for testing at home. The seed updates rows and removes zones dropped from `zones.yaml`; cameras are never deleted (that would delete their incidents) |
| `tools/camera_share.py` | Runs on the second laptop: shares its webcam on the Wi-Fi as MJPEG at `:8090/video`, like the IP Webcam app on a phone. Needs only Python and OpenCV |
| `knowledge/` | SOP and contact documents for the assistant (`SAMPLE-sop.md` is invented; replace it) |
| `alembic/versions/` | Database migrations |
| `tests/` | pytest suite (section 10). `tests/fixtures/` holds a frozen copy of the config, so editing `config/*.yaml` for the demo never breaks a test |

## 3. Data model (PostgreSQL)

| Table | Key columns | Notes |
|---|---|---|
| `cameras` | id (e.g. `cam-webcam`), name, source, port | From `cameras.yaml` |
| `zones` | camera_id, name, kind (`restricted`/`ppe`/`work`), polygon (fractions 0..1), max_people | From `zones.yaml`; editable via `/zones` |
| `users` | name, role (label), pin_hash (bcrypt), camera_ids[], actions[], active | Permissions are data. `all_cameras` in actions means every camera |
| `sessions` | token_hash (SHA-256), user_id, expires_at | Opaque login tokens, 12 h |
| `responders` | name, phone, zone, shift_start/end, call_order, active, user_id | The phone book. `user_id` links a responder to a user, which gives the role and cameras the ladder uses |
| `incidents` | type, severity, status, camera_id, zone, started_at, confidence, snapshot_path, clip_path, acknowledged_by(_user_id) | Status: open → acknowledged → resolved / false_alarm |
| `timeline_events` | incident_id, at, action, target (`to`), actor (`by`), via | Every step of an incident |
| `audit_log` | at, user_id, user_name, action, target, channel, detail | Who, what, when, which channel |
| `roll_calls` | incident_id, message, started_at, started_by, channel | One per "alert everybody" |
| `calls` | call_sid, kind (`alert`/`roll_call`/`broadcast`/`direct`), incident_id, roll_call_id, responder_id, to_name, to_phone, message, audio_file, status, response | One row per phone call. `response` stores the keypress or intent, which makes webhooks idempotent |

Migrations run in order with `alembic upgrade head`. The latest one is `571bac6e747d_calls_and_roll_calls`.

## 4. Flows in detail

### 4.1 Worker pipeline (per camera)
1. **Capture thread.** Reads the camera nonstop. It overwrites `latest` with every frame and `keyframe` with every 3rd frame. If the camera is lost, it retries every 5 s.
2. **Main loop.** Takes the keyframe, resized for `imgsz=480`.
   - `yolo11n-pose` with ByteTrack gives person boxes, track IDs and 17 keypoints.
   - The torso angle comes from the shoulder midpoint to the hip midpoint.
   - The bag detector (`yolo11n.pt`, classes backpack/handbag/suitcase) runs about once a second.
   - The PPE and fire detectors run only if their model paths are set.
3. **Rules engine.** `engine.update(now, people, bags, ppe, fire)` returns the incidents confirmed on this frame. Each rule keeps a timer per track or zone, and a 60 s cooldown applies per camera and type.

| Rule | Fires when | Config |
|---|---|---|
| access | Feet point (bottom-centre of the box) inside a `restricted` zone for 3 s. Severity is critical between 20:00 and 06:00 IST, otherwise high | `ACCESS_DWELL_S`, `NIGHT_START/END` |
| loitering | Same track in one zone for 120 s | `LOITER_S` |
| crowding | More than `max_people` people in the zone | per zone in `zones.yaml` |
| fall | Torso ≥ 60° from vertical AND hips dropped ≥ 25% of the upright height, held for 5 s. Bending fails the hip test | `FALL_*` |
| unattended | Bag still (moved < 3% of the frame width) for 60 s with no person within 15% | `UNATTENDED_S`, `BAG_*` |
| ppe | Person in a `ppe` zone with no helmet overlapping the top of the box, or no vest overlapping its middle, for 10 analysed frames in a row | `PPE_MODEL_PATH`, `PPE_MISSING_FRAMES`, label lists |
| fire | Fire or smoke label with confidence ≥ 0.5 for 5 analysed frames in a row | `FIRE_MODEL_PATH`, `FIRE_FRAMES` |

4. **Incident.** `worker/incidents.py` does the following in order:
   1. inserts the row and a `detected` timeline entry;
   2. saves `media/<id>.jpg`;
   3. calls the API;
   4. 5 s later, writes `media/<id>.mp4` from the ring buffer (5 s before plus 5 s after) using FFmpeg `-c:v libx264 -pix_fmt yuv420p -movflags +faststart`, then fills `clip_path`.

### 4.2 Escalation timing (the call ladder)
`escalation.start(id)` runs when the API hears about a new incident. It looks at the severity:

| Severity | 0 s | 60 s | 120 s |
|---|---|---|---|
| critical | Call the Head Guard + WhatsApp the Admin (with a snapshot link) | Call the Admin | Call the Manager |
| high | Call the Head Guard + WhatsApp the Admin | Call the Manager | — |
| low | WhatsApp the zone manager. No calls | — | — |

Low-severity rule: if the same camera and zone has 3 or more incidents within 60 minutes (counted up to this incident), the Head Guard is called once.

Every step does the following:
1. **Re-reads the incident.** If it is no longer `open` (acknowledged, resolved or false alarm), the step does nothing.
2. **Picks the contact.** It uses `directory.contacts_for_role(role, camera_id, zone)`, which keeps active responders whose linked user has that role and can see the camera. They are sorted with on-shift people first, then the incident's zone, then `call_order`.
3. **Records the step.** A later call step first writes an `escalated` timeline entry. The call itself writes `call_placed` (or `call_failed`), and each WhatsApp writes `whatsapp_sent`. Every entry is published on the WebSocket.

If nobody fits a role, the step logs a warning in the API window and is skipped. The ladder carries on with the next step. A call step dials only the first contact; a WhatsApp step messages every matching contact.

Stop conditions: an acknowledgement from any channel calls `escalation.stop(id)`. That removes the jobs, cancels calls still in the queue and hangs up active alert calls for that incident, except the call that just acknowledged.

### 4.3 Twilio call lifecycle
1. `calls.place(kind, to, message, incident_id, ...)` inserts a `calls` row and queues it.
2. The dispatcher thread waits for its rate slot, then POSTs to Twilio `Calls.json` with:
   - `Url=PUBLIC_BASE_URL/twilio/voice?call_id=N`
   - `StatusCallback=PUBLIC_BASE_URL/twilio/status?call_id=N`
   - `Timeout=30`
3. When the person answers, Twilio requests `/twilio/voice`. We answer with TwiML: `<Say>` the alert (English, `en-IN`, starting immediately), then `<Gather input="speech dtmf" numDigits="1">` pointing at `/twilio/gather`.
4. `/twilio/gather` reads the keypress or speech:

| Call kind | 1 | 2 | 3 | 9 | Speech |
|---|---|---|---|---|---|
| alert | acknowledge ("going") | false alarm (high only; critical refuses) | `<Dial>` the supervisor | repeat | Keyword match first ("going", "coming", "false alarm", "repeat", "alert everybody"...), then the LLM `classify_intent` |
| roll_call | responding | safe / evacuated | — | repeat | "coming", "safe", "evacuated"... |
| broadcast | — | — | — | repeat | `<Play>` the uploaded audio |

5. **"Alert everybody" on a call.** A Head Guard or Admin says "alert everybody". We ask "say yes or press 1 to confirm". Only a user with the `alert_all` action can launch it.
6. **Final status.** `/twilio/status` records it (completed, busy, no-answer, failed). A final status without an answer counts as "not reached" on the roll-call board.

Safety properties:
- **Signatures.** Every webhook checks `X-Twilio-Signature` (HMAC-SHA1 of the public URL plus the sorted form fields, keyed with `TWILIO_AUTH_TOKEN`). Unsigned or forged requests get 403. Without a token configured, they get 503.
- **Idempotency.** `calls.response` is set once. A repeated "1" never acknowledges twice and never writes a second timeline row.
- **Who acknowledged.** Phone actions take the person from the call row (`responder_id`), never from request data.
- **Critical incidents** can only be acknowledged by phone. Closing one needs the dashboard.
- **Errors.** Any error inside a webhook returns a polite "Sorry, something went wrong" TwiML. It never returns a 500 that Twilio would read aloud as an application error.
- **Retries.** A Twilio 4xx (`TwilioRejected`, e.g. an unverified number on a trial account) is not retried. A 5xx or network error is retried once.

### 4.4 Alert everybody and roll call
`POST /alerts/all {message, incident_id?}` (needs `alert_all`) does the following:
1. creates a `roll_calls` row;
2. WhatsApps every on-shift responder in parallel;
3. queues a `roll_call` call to each of them, at 1 per second;
4. publishes `{kind:"alert_all", ...}` on the WebSocket to every user, which the dashboard shows as a red banner;
5. returns the counts.

`GET /roll-call?incident_id=` returns the board: total, responded, safe, pending and not_reached, plus a row per guard. Each keypress publishes a `{kind:"roll_call", ...}` update.

The same flow can start from three places: the dashboard, a phone call (spoken "alert everybody" plus a confirm), or the assistant ("call everyone" plus a spoken "yes").

### 4.5 Broadcast
`POST /broadcast` takes multipart `zone` and `audio` (MP3 or WAV, at most 5 MB).
- The file is saved as `media/broadcast-<uuid>.<ext>`.
- Each active responder in that zone gets a `broadcast` call that `<Play>`s it. Twilio fetches the file through `/share/media/...` with a signed link.

### 4.6 Assistant: questions
`POST /assistant/query {question}` works like this:
1. **Question to filter.** `parse_question` asks the LLM for a JSON filter `{types, cameras, severities, statuses, from, to, limit}`.
   - It uses Groq (5 s timeout, one retry), then Ollama, then the keyword parser.
   - Pydantic validates the filter. Anything invalid falls back to the keyword parser.
   - The LLM never writes SQL.
2. **Filter to rows.** `run_filter` builds the SQLAlchemy query. It always adds the user's camera scope and caps the result at 50 rows.
3. **Rows to answer.** `answer_text` builds the reply from the rows: a count by type plus the latest incident. It cannot invent an incident.
4. **Response.** It includes the `source` (`groq`/`ollama`/`keywords`) and `ms`.

`POST /assistant/voice` (multipart audio, at most 10 MB) first transcribes the audio (Groq Whisper, then local faster-whisper "small"), then follows the same path. It returns the transcript too.

### 4.7 Assistant: commands and confirm
`POST /assistant/command {text}` tries three paths in order:
1. **Fast path.** About 13 regex patterns: call everybody / a person / a zone, broadcast a message, status report, show camera N, last hour, acknowledge or resolve incident N, stop all calls, who has not responded, call the fire station, plus SOP questions.
2. **Smart path.** Groq tool calling chooses one of the same intents.
3. **Knowledge path.** Anything left goes to the RAG.

Rules on top of the paths:
- **Permissions.** Each intent needs an action (`NEEDS`). A user without that action gets `status:"refused"`.
- **Big actions** (`alert_all`, `evacuate`, `call_fire_station`) return `status:"needs_confirmation"` and a `confirm_token`, valid for 60 s and only for the same user. `POST /assistant/confirm {token}` runs the action. A token works once.
- **Fire station.** It is only called when a human gives the order and then confirms. The number comes from `FIRE_STATION_NUMBER` and the address read out from `SITE_ADDRESS`. If the number is blank, the assistant refuses and says so. 112 is never called.
- **Critical incidents.** The assistant can acknowledge any incident, but it refuses to resolve a critical one ("use the dashboard").

### 4.8 Knowledge (RAG)
1. Files in `backend/knowledge/` (`.md`/`.txt`, except README) are split into chunks of about 600 characters. Each chunk keeps its heading.
2. A question is matched by TF-IDF cosine similarity. The top 3 chunks with a score of at least 0.08 are kept.
3. Those chunks go to the LLM with the instruction "answer ONLY from this context".
4. With no LLM available, the best chunk is returned as it is. With no match, the answer is "I couldn't find that in our documents."

Files are re-read automatically when they change. No restart is needed.

### 4.9 Heartbeat watchdog and digest
- **Watchdog.** Workers post a heartbeat every 5 s. Every 10 s the API checks each camera. A camera silent for 30 s gets one WhatsApp to the Admin ("camera X has been silent for 30 s"). It is not repeated until the camera has come back and gone silent again.
- **Digest.** At 18:00 IST, a WhatsApp goes to every Manager with the low-severity incidents of the last 24 h, counted by type and zone.

### 4.10 Adding and installing a camera (P22)
1. `POST /cameras/test {source}` opens the address with OpenCV on a side thread and reads one frame (gives up after `CAMERA_PROBE_TIMEOUT_S`).
2. `POST /cameras` tests again (unless `test_first=false`), saves the row with the next free port (from `FIRST_WORKER_PORT`) and number, adds the camera to every user of `role_names`, and audits `camera_add`.
3. With `start=true`, `services/workers.py` launches `python -m worker.run_worker --camera <id>` as a child process; output goes to `logs/worker-<id>.log` (`GET /cameras/{id}/worker-log`).
4. The worker reads its camera from the database (`services/camera_registry.py`; cameras.yaml only if the database is down), so nothing is edited by hand. Its heartbeat makes it online in `/health`, `/cameras` and `/overview`.
5. `WORKERS_AUTOSTART=true`: the API starts every enabled camera at boot and restarts a crashed worker after `WORKER_RESTART_S`. A worker someone stopped is not restarted. Stopping the API stops its workers.

### 4.11 Duty check-in and the call ladder (P23)
- A guard's pass is `"<user id>.<HMAC>"` signed with `STREAM_SECRET` (set it, or passes change at every restart). A post code is the same for an area id, inside a `DASHBOARD_URL/check-in?post=` link.
- `services/duty.py`: the latest `duty_events` row per guard; on duty = "in" and under `DUTY_MAX_H` old.
- `services/directory.contacts_on_duty()` returns on-duty people of a role. If there are none, it returns the shift roster with `fallback=True`; the ladder then writes `duty_fallback` on the timeline and WhatsApps the Head Guards once per incident. Only `DUTY_ROLES` check in; Admin and Manager are always callable.
- Alert everybody, broadcast and "call zone" use `on_shift_contacts_with_fallback()`: off-duty guards are left out unless no guard at all is on duty.

### 4.12 Activity, overview, usage and voice summaries (P24)
- The audit log now also holds `assistant_query` and `assistant_command` rows (text, answer, intent, status, source) and `camera_view` rows (from the stream proxy, one per user and camera per `CAMERA_VIEW_DEDUPE_S`). `/activity` groups them per person. View: `view_audit` only.
- `/overview` and `/usage` are computed on request; nothing extra is stored. Summary history is any past window of `/summary?from&to`, rebuilt from the incidents.
- Voice summaries: the fast path matches "summary/recap", then `summary_window()` reads "today", "yesterday", "past/last N hours/minutes" and `summary_cameras()` reads "cameras 6, 7 and 8". Unknown or hidden camera numbers are named as "not found".
- `services/tts.py`: Rumik Silk over HTTP (`POST {RUMIK_GATEWAY_URL}/v1/tts`, Bearer key, WAV back), 8 s timeout and one retry. `python -m tools.check_rumik` checks the key without printing it.

## 5. Security model

| Area | How it is protected |
|---|---|
| Login | 4–8 digit PIN, bcrypt hash. Token = 32 random bytes; the database stores only its SHA-256. Expires after 12 h. 5 wrong PINs per minute per client gives 429 |
| Authorisation | `require(action)` gives 403. Every incident query adds the camera scope; an incident outside your cameras gives 404 (it looks like it doesn't exist) |
| Live video | `stream_url` carries an HMAC token (camera, user, expiry 1 h), checked against the user's current cameras |
| WebSocket | `?token=` login token. Each message is filtered by camera. Site-wide notices (`kind`) go to everyone |
| Worker → API | `X-Internal-Key` header must match `INTERNAL_KEY` |
| Twilio webhooks | Signature check (section 4.3) |
| Public address (ngrok) | Middleware: requests whose Host is the `PUBLIC_BASE_URL` host may only reach `/twilio/*` and `/share/*`. Everything else gives 404 |
| WhatsApp images | `/share/media/{name}?st=`: HMAC link, 24 h, safe file names only |
| Audit | Every action writes an `audit_log` row in the same database transaction |
| Errors | A generic 500 handler logs the stack trace and returns a plain message |
| Secrets | Only in `.env` (gitignored). `.env.example` has empty values |

Known risks and accepted trade-offs:
- **`/media` is unauthenticated on localhost.** Anyone on the laptop can open a snapshot by its URL. ngrok cannot reach it because of the guard. This is acceptable for a demo laptop.
- **The Twilio trial** plays "You have a trial account..." and waits for a keypress before our message starts. Tell the judges, or upgrade the account.
- **Licences.** Ultralytics YOLO is AGPL-3.0. That is fine for a hackathon demo, but needs a commercial licence for a product. See `docs/disclosures.md`.
- **ngrok free URLs change** on every start. Update `PUBLIC_BASE_URL` and restart the API each time.
- **A short `STREAM_SECRET`** makes tokens guessable. Use a long random value; the command is in section 6.

## 6. `.env` keys

| Key | Secret? | What it is | If blank |
|---|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://user:pass@localhost:5432/safetyai` | The API cannot start |
| `INTERNAL_KEY` | yes | Shared between workers and the API | Worker notices are rejected |
| `PUBLIC_BASE_URL` | no | ngrok URL, e.g. `https://abcd.ngrok-free.app` | No live calls (Twilio can't reach us); no WhatsApp image links |
| `TWILIO_ACCOUNT_SID` | yes | From the Twilio console (`AC...`) | Calls off (or simulated with `CALLS_MODE=simulate`) |
| `TWILIO_AUTH_TOKEN` | yes | From the Twilio console; also signs webhooks | Calls off; webhooks give 503 |
| `TWILIO_FROM_NUMBER` | no | Your Twilio number, `+1...` | Calls off |
| `WHATSAPP_API_URL` | no | Send endpoint (port from Bhoomi) | WhatsApp off (or simulated with `WHATSAPP_MODE=simulate`) |
| `WHATSAPP_TOKEN` | yes | Bearer token | WhatsApp off |
| `GROQ_API_KEY` | yes | console.groq.com | Uses Ollama, then keywords |
| `GROQ_MODEL` | no | A chat model name from the Groq console that supports JSON mode and tool calling | Groq skipped |
| `GROQ_STT_MODEL` | no | A Whisper model name from the Groq console | Uses local faster-whisper |
| `OLLAMA_URL` | no | `http://localhost:11434` | Ollama skipped |
| `OLLAMA_MODEL` | no | `llama3.2:3b` | Defaults to `llama3.2:3b` |
| `OFFLINE_MODE` | no | `true` skips all cloud AI | Online |
| `MEDIA_DIR` | no | Where clips and snapshots go | `media` |
| `STREAM_SECRET` | yes | Long random string (`python -c "import secrets;print(secrets.token_hex(32))"`) | Random per start; links die on restart |
| `DEMO_USER_PINS` | yes | `anita=1111,vikram=2222,ravi=3333,suresh=4444` | The seed skips users without a PIN |
| `DEMO_RESPONDER_PHONES` | yes | `anita=+9198...,ravi=+9198...` (must be Twilio-verified on a trial) | The seed skips responders without a phone |
| `FIRE_STATION_NUMBER` | no | Only dialled when a human orders it and confirms | That command is refused |
| `SITE_ADDRESS` | no | Read to the fire station | The call says "our site" instead; fill it in |
| `PPE_MODEL_PATH` | no | `models/ppe.pt` | PPE rule off |
| `FIRE_MODEL_PATH` | no | `models/fire.pt` | Fire rule off |
| `CALLS_MODE` | no | `auto` / `live` / `simulate` / `off` | `auto` |
| `WHATSAPP_MODE` | no | Same four values | `auto` |

Non-secret tuning values: every rule threshold, delay and limit in `app/config.py` can be overridden from `.env` by its upper-case name, e.g. `ACCESS_DWELL_S=2`. The commented list is in `.env.example`. The important ones:

| Value | Default | Value | Default |
|---|---|---|---|
| `NIGHT_START` / `NIGHT_END` | 20:00 / 06:00 | `DIGEST_TIME` | 18:00 |
| `CRITICAL_ADMIN_DELAY_S` | 60 | `CRITICAL_MANAGER_DELAY_S` | 120 |
| `HIGH_MANAGER_DELAY_S` | 60 | `LOW_REPEAT_COUNT` / `_WINDOW_MIN` | 3 / 60 |
| `HEARTBEAT_TIMEOUT_S` | 30 | `TWILIO_CPS` | 1.0 |
| `CALL_RING_TIMEOUT_S` | 30 | `CALL_LANGUAGE` | en-IN |
| `INCIDENT_COOLDOWN_S` | 60 | `DETECT_EVERY_N_FRAMES` / `DETECT_IMGSZ` | 3 / 480 |
| `SESSION_TTL_H` | 12 | `STREAM_TOKEN_TTL_S` | 3600 |
| `ROLE_ADMIN` etc. | Admin, Manager, Head Guard, Guard | `ASSISTANT_CONFIRM_TTL_S` | 60 |

## 7. Modes and fallback chains

**`CALLS_MODE` / `WHATSAPP_MODE`:**
- `auto` (the default): live if the credentials (and, for calls, `PUBLIC_BASE_URL`) are set, otherwise **off**. Set `simulate` yourself for rehearsals.
- `live`: always real. Missing keys turn into a logged failure.
- `simulate`: nothing leaves the laptop. Rows, timeline and WebSocket updates still happen, so the dashboard looks exactly the same. A good choice for rehearsals.
- `off`: no calls or messages and no timeline entries for them. The dashboard and WebSocket still alert.

**Fallback chains:**
- LLM: Groq (5 s, one retry) → Ollama (10 s) → keyword parser / regex. The answer's `source` says which one answered.
- Speech to text: Groq Whisper → faster-whisper "small" on the CPU. The first local run downloads about 500 MB, so do it once before the event.
- Alerting: Twilio call → WhatsApp and the dashboard still alert if Twilio fails.
- Vision: OpenVINO export (about 2x faster) → plain `.pt` model.

## 8. API summary
Everything is in `docs/CONTRACT.md`: endpoints, request and response shapes, the incident object, enums, timeline actions, auth rules, and WebSocket messages.
- **P1–P18** are proposed clarifications (P11–P18 were added on 21 Oct: WebSocket notices, alert-all, roll call, broadcast, assistant fields, share links, public address, health). They need your sign-off, and the teammate needs to see them.
- Interactive docs: `http://localhost:8000/docs` (local only; the public-address guard blocks them over ngrok).

## 9. Running on Windows (first full run)

```powershell
cd backend
py -3.11 -m venv .venv ; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then fill in the values (section 6)
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000
# second window:
python -m worker.run_worker --camera cam-webcam
# one-time, makes detection about 2x faster:
yolo export model=models/yolo11n-pose.pt format=openvino imgsz=480
yolo export model=models/yolo11n.pt format=openvino imgsz=480
```

For the 3-camera stage setup (2 Android phones + a second laptop), follow `docs/DEMO_SETUP.md`.

Needed on the machine:
- PostgreSQL 16 (create the database named in `DATABASE_URL`);
- FFmpeg on PATH (`ffmpeg -version`);
- for offline AI: Ollama, then `ollama pull llama3.2:3b`.

Setting up live calls:
1. **Twilio console.** Copy the Account SID and Auth Token, buy or get a trial number, and verify each phone you will call (trial accounts can only call verified numbers). No webhook needs setting on the number: our outbound calls send their own URLs.
2. **ngrok.** Run `ngrok http 8000` and copy the `https://...` URL into `PUBLIC_BASE_URL`. Restart the API. A free URL changes every time.
3. Set `DEMO_RESPONDER_PHONES` and rerun `python -m app.seed`.
4. **Check.** `GET /health` should show `"twilio":"ok"`. Trigger a test incident and your phone rings within about 15 s.

Setting up WhatsApp: copy the send function from Bhoomi into `app/services/whatsapp.py` where it says `PORT FROM BHOOMI` (the URL, headers and payload). Then set `WHATSAPP_API_URL` and `WHATSAPP_TOKEN`.

## 10. Testing

```powershell
pytest            # all tests; uses a separate <db>_test database (tests/conftest.py)
pytest tests/test_escalation.py -q
```

| File | Covers |
|---|---|
| `test_config.py`, `test_health.py` | Settings, `/health` |
| `test_capture.py`, `test_buffer.py`, `test_pose.py` | Capture thread, ring buffer and clip, torso angle |
| `test_rules.py`, `test_rules_ppe_fire.py` | Every rule with fake detections, cooldown, night window |
| `test_internal.py` | Worker → API notice, internal key |
| `test_api_gate2.py` | Login, roles, camera scope, stream tokens, ack/resolve, audit, WebSocket |
| `test_escalation.py` | Ladders per severity, stop on ack, low-repeat, digest, heartbeat watchdog (fake scheduler) |
| `test_twilio_alerts.py` | Signatures, TwiML, keypad/speech, idempotency, alert-all, roll call, broadcast, public-address guard |
| `test_calls.py` | Twilio client retry rules, rate limit, cancel (fake Twilio) |
| `test_demo_config.py` | The real demo config: unique ids and ports, zones inside the frame and on real cameras, users and roster consistent; the seed removes dropped zones |
| `test_assistant.py` | Keyword filter, LLM fallback, answers, commands, confirm tokens, permissions, knowledge |

Last run: **140 passed**. The tests never call real Twilio, WhatsApp or Groq. They use simulate mode or fakes.

Manual checks still to do on your laptop (they can't be automated here):
- a real webcam and a phone camera (IP Webcam app: `http://PHONE-IP:8080/video`);
- a real Twilio call to a verified phone, with keypresses 1/2/3/9 and speech;
- a real WhatsApp message with a snapshot;
- Groq and Ollama answers, and the first faster-whisper download;
- the speed targets: incident to dashboard < 2 s, to phone ringing < 15 s;
- two cameras at 8+ analysed FPS each.

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| API won't start: database error | PostgreSQL not running or wrong `DATABASE_URL` | Start the PostgreSQL service and check the URL |
| `relation ... does not exist` | Migrations not run | `alembic upgrade head` |
| Login always 401 | Seed not run, or a PIN missing from `DEMO_USER_PINS` | Fill the PINs, then `python -m app.seed` |
| Login 429 | 5 wrong PINs in a minute | Wait 60 s |
| `/health` camera offline | Worker not running or crashed | Start the worker and read its window |
| Stream black / 403 | Stream token expired or camera not delegated | Reload `/cameras` for a fresh `stream_url` |
| No clip, only a snapshot | FFmpeg not on PATH | Install FFmpeg, check `ffmpeg -version` |
| Detection slow | No OpenVINO export | Run the two `yolo export` commands |
| `twilio: not_configured` | SID, token or number blank | Fill the `.env` and restart |
| Phone never rings | `PUBLIC_BASE_URL` blank or stale, number not verified, or `CALLS_MODE` is `simulate`/`off` | New ngrok URL, verify the number, set the mode to `auto` or `live` |
| Call says "application error" | Webhook unreachable (ngrok down) | Restart ngrok, update the URL |
| Webhook 403 | Signature mismatch: `PUBLIC_BASE_URL` differs from the URL Twilio used | Make them identical (https, no trailing slash) |
| Assistant `source: keywords` | No Groq key and Ollama not running | Fill `GROQ_*` or start Ollama |
| Voice 503 | No Groq STT and faster-whisper missing or not downloaded | Install `faster-whisper` and run once online |
| SOP answer "couldn't find" | No matching text in `knowledge/` | Add the real SOP files |

## 12. Limitations, gaps and your TODOs

| Item | Status | What you need to do |
|---|---|---|
| Twilio keys, number, verified phones | Blank | Fill `.env`, verify the numbers |
| `PUBLIC_BASE_URL` | Blank | Run ngrok each session |
| WhatsApp sender | Placeholder (Meta Cloud format) | Port the Bhoomi function |
| Groq key and model names | Blank | Pick models from the Groq console; add them to `disclosures.md` |
| Demo phones | Blank | `DEMO_RESPONDER_PHONES`, then reseed |
| Fire station number, site address | Blank | Decide if you want it in the demo (never 112) |
| PPE and fire/smoke models | Not chosen (licence decision) | Pick or train (Colab), set the paths, add to `disclosures.md` |
| Real SOPs and contacts | Sample only | Replace `knowledge/SAMPLE-sop.md` |
| Contract P1–P18 | Proposed | Sign off; tell the teammate |
| Default branch `main` | Not set | GitHub → Settings → Default branch |
| Offline speech to text | Untested here (model host blocked in this sandbox) | Run once online on your laptop |
| Real cameras | Untested | Webcam and phone test |
| Rumik voice | Built, untested (no key in the sandbox) | Add `RUMIK_GATEWAY_URL` next to the key, run `python -m tools.check_rumik` |
| Duty QR screens | Backend only | The teammate builds "My pass", the scanner page and `/check-in?post=` |
| Wake word | Not built (lowest priority) | Push-to-talk works; the frontend sends audio to `/assistant/voice` |
| Pre-recorded "on it" sound | Frontend side | The teammate plays it the moment push-to-talk ends |
| Tamil/Hindi in calls | Not at call start (by decision) | Optional later |

## 13. Decision log (short)

| Date | Decision |
|---|---|
| 18 Oct | Permissions are data (role label + cameras + actions); there are no hard-coded roles. Admin is the top call level; no CEO |
| 18 Oct | Critical ladder: Head Guard at 0 s, Admin at 60 s, Manager at 2 min. High: Head Guard, then Manager at 60 s; the Admin gets WhatsApp only. Low: WhatsApp the zone manager, plus a Head Guard call on 3 repeats per hour |
| 18 Oct | Calls open in English with `<Say>`. Phone and voice can only acknowledge critical incidents; closing needs the dashboard |
| 18 Oct | Big assistant actions need a spoken "yes"; calling one person or one zone does not |
| 19 Oct | Opaque tokens (random, hashed in the database) instead of JWT, so logout and expiry are instant |
| 19 Oct | Cloud text alerts (Twilio, WhatsApp, Groq) are fine; video never leaves the laptop. Colab is the training fallback |
| 19 Oct | Latest-frame capture thread; analyse every 3rd frame at 480 px; OpenVINO when exported |
| 20 Oct | Stream tokens signed with HMAC, 1 h, re-checked against current delegation |
| 21 Oct | Twilio over plain httpx (no SDK); a call queue at 1 per second; simulate mode for rehearsals |
| 21 Oct | The LLM only ever produces a validated JSON filter; answers are built from rows; keyword fallback always works |
| 21 Oct | RAG with TF-IDF (no model download, works offline) |
| 21 Oct | The public-address guard: the ngrok URL exposes only `/twilio/*` and `/share/*` |
