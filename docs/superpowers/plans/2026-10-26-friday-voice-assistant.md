# Friday Voice Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Friday, a spoken assistant with a female Rumik Silk voice: the wake word opens a glowing orb, she answers and acts on spoken orders, she announces alarms with a red glow, and Admin/Manager calls depend on whether they are on the software.

**Architecture:** The backend gains a voice-command endpoint (speech to text, then the existing `commands.handle`, then cached Rumik audio), `friday` WebSocket notices, and a presence-aware call ladder (the WebSocket manager knows who is online). The Python listener on the laptop runs the wake, record, send, play loop and plays audio itself; the React dashboard draws the orb, the red alarm glow and push-to-talk, and plays audio only when the listener is not running.

**Tech Stack:** Python 3.11, FastAPI, APScheduler, httpx, openWakeWord, sounddevice; React 19, Vite, Tailwind 4, framer-motion, TanStack Query.

**Spec:** `docs/superpowers/specs/2026-10-26-friday-voice-assistant-design.md`

## Global Constraints

- Assistant name `FRIDAY_NAME=Friday` (settings); product name stays `PRODUCT_NAME` (RingGuard). "Jarvis" never appears in user-facing text.
- Voice: Rumik `RUMIK_MODEL=mulberry`, `RUMIK_SPEAKER`, `RUMIK_DESCRIPTION` from `.env`; reply text only leaves the laptop, never video.
- On-screen grace `ONSCREEN_GRACE_S=5`. Critical: Head Guard call 0 s; Admin call 0 s (5 s if on screen); Manager call 5 s (10 s if on screen). High: Head Guard 0 s; Manager 0 s (5 s if on screen); Admin WhatsApp only. All WhatsApp steps unchanged. Never call above Admin.
- Big actions (alert everyone, evacuate, call fire station) still need a spoken "yes"; nothing calls 112 or the fire station by itself; Friday advises during alarms and never acts by herself.
- Listener acts as `FRIDAY_USER` (a user name in `.env`) via `X-Internal-Key`; that user's actions decide what Friday may do.
- `speaker` is `listener` when a listener heartbeat arrived in the last 15 s, else `dashboard`.
- Every endpoint change goes into `docs/CONTRACT.md` (P25) in the same task; new settings go into `backend/.env.example`; new external services or models go into `docs/disclosures.md`.
- No new frontend dependencies. Python dependencies: exact pins only (none expected).
- Checks before each commit: `pytest` (backend) and `npm run build && npm run lint` (frontend, no new errors). Push after each task (CLAUDE.md rule 10).

## Review Focus

1. A dashboard and the listener both running: Friday's voice must be heard once, from the listener. Test in Task 3 (`test_speaker_is_listener_while_heartbeat_is_fresh`).
2. Rumik down or slow during a fire: the alarm notice still goes out at once with `audio_url: null`, and calls are not delayed. Test in Task 3 (`test_alarm_goes_out_without_audio_when_rumik_fails`).
3. Listener crashed: after 15 s without a heartbeat, `speaker` falls back to `dashboard`. Test in Task 3 (`test_speaker_falls_back_when_heartbeat_is_stale`).
4. Silence, coughing or noise after the wake word: no crash, the reply is "Sorry, say that again", and the listener goes back to waiting. Tests in Task 3 (`test_voice_command_with_no_speech_says_sorry`) and Task 4 (`test_session_with_silence_returns_to_waiting`).
5. Acknowledging during the grace period from any channel (screen, voice, keypad): no call; the timeline says `call_skipped_on_screen`. Test in Task 1 (`test_ack_during_grace_skips_the_call`).

---

### Task 1: Presence-aware call ladder

**Files:**
- Modify: `backend/app/ws.py` (remember the user per connection)
- Modify: `backend/app/routers/events.py:124-140` (pass `user.id` to `manager.connect`)
- Modify: `backend/app/services/escalation.py` (`Step`, `ladder_for`, `start`, `run_step`)
- Modify: `backend/app/config.py` (add `onscreen_grace_s: float = 5.0`; remove `critical_admin_delay_s`, `critical_manager_delay_s`, `high_manager_delay_s`)
- Modify: `backend/app/schemas.py` (`TimelineAction` adds `call_skipped_on_screen`)
- Modify: `backend/tests/test_escalation.py` (new ladder values)
- Test: `backend/tests/test_friday_ladder.py`
- Docs: `CLAUDE.md` ("Escalation and calls" and "Config values" to the new timings), `docs/CONTRACT.md` (timeline action, P25 ladder note), `backend/.env.example` (`ONSCREEN_GRACE_S=5`)

**Interfaces:**
- Produces: `ConnectionManager.connect(websocket, allow, user_id: int | None = None)`, `ConnectionManager.online_user_ids() -> set[int]`.
- Produces: `Step(delay_s: float, action: str, who: str, defer_on_screen: bool = False)`.
- Produces: `Escalation.run_step(incident_id: int, index: int, deferred: bool = False) -> None`.

- [ ] **Step 1: Write the failing tests** in `test_friday_ladder.py` (reuse `FakeScheduler` and `timeline` from `test_escalation.py`; check in "Ravi Kumar" with `check_in`):
  - `test_ladders_use_the_onscreen_rule`: `[(st.delay_s, st.action, st.who, st.defer_on_screen) for st in ladder_for("critical", s)] == [(0,"call","head_guard",False),(0,"whatsapp","admin",False),(0,"call","admin",True),(5,"call","manager",True)]`; high `== [(0,"call","head_guard",False),(0,"whatsapp","admin",False),(0,"call","manager",True)]`.
  - `test_admin_away_from_the_software_is_called_at_once`: run step 2 of a critical incident with `manager.online_user_ids()` empty; the timeline gains `("escalated","Anita Rao"),("call_placed","Anita Rao")` and no new job is scheduled.
  - `test_admin_on_screen_gets_the_grace_period`: monkeypatch `manager.online_user_ids` to return Anita's id; run step 2; no call placed; a job `ladder:{id}:2` exists with `run_date` 5 s later and `args == [id, 2, True]`.
  - `test_ack_during_grace_skips_the_call`: as above, then set the incident to acknowledged and run the deferred job; no call; timeline ends with `("call_skipped_on_screen","Anita Rao")`.
  - `test_onscreen_but_no_reaction_is_called_after_grace`: run the deferred job with the incident still open; `("call_placed","Anita Rao")` is added.
  - `test_ws_manager_tracks_online_users`: connect two fake websockets with user ids 1 and 2, disconnect one; `online_user_ids() == {2}`.
- [ ] **Step 2: Run them, expect failures:** `cd backend; pytest tests/test_friday_ladder.py -v` (ImportError or assertion failures).
- [ ] **Step 3: Implement.** `run_step`: for a `defer_on_screen` step, when `deferred` is False and `contacts[0].user_id in manager.online_user_ids()`, schedule `run_step(id, index, True)` at now + `onscreen_grace_s` (job id `ladder:{id}:{index}`, so `stop()` removes it) and return. In a deferred run whose incident is no longer open, add timeline `call_skipped_on_screen` (to the target's name), commit, publish. Update `test_escalation.py` expected delays to `[0, 0, 0, 5]`.
- [ ] **Step 4: Run** `pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit and push** `feat(escalation): call people away from the software at once, 5 s grace on screen`.

### Task 2: Friday's voice, cached

**Files:**
- Modify: `backend/app/services/tts.py`
- Modify: `backend/app/config.py` (`rumik_model="mulberry"`, add `rumik_description: str = ""`, `friday_name: str = "Friday"`, `tts_cache_dir: str = "tts"` under media)
- Create: `backend/app/services/phrases.py` (the sentences Friday says, one place)
- Modify: `backend/app/main.py` (pre-render in a background thread at startup)
- Modify: `backend/tools/check_rumik.py` (`--voices` prints time and saves one sample per `RUMIK_SPEAKER` candidate given on the command line)
- Test: `backend/tests/test_tts_cache.py`
- Docs: `.env.example` (`RUMIK_DESCRIPTION`, `FRIDAY_NAME`), `disclosures.md` (Rumik mulberry voice line updated)

**Interfaces:**
- Produces: `tts.cached(text: str) -> tuple[str | None, int]`: `(media URL like "/media/tts/<sha1>.wav" or None, ms)`. Never raises; None when Rumik is not set up or fails.
- Produces: `tts.cache_path(text: str) -> Path` (key = sha1 of model, speaker, description, text).
- Produces: `phrases.YES_BOSS = "Yes, boss."`, `phrases.SORRY = "Sorry, say that again."`, `phrases.CONFIRM = "Say yes to confirm."`, `phrases.alarm(incident_type: str, camera_name: str, camera_number: int | None, calling: str | None) -> str`, `phrases.calling(name: str) -> str`, `phrases.all_common(db) -> list[str]` (yes/sorry/confirm, an alarm line per critical/high type and camera, a calling line per active responder).
- `phrases.alarm("fire","Shop Floor",2,"Ravi Kumar") == "Fire detected on Shop Floor, camera 2. Calling Ravi Kumar. Say evacuate to alert everyone, or show camera 2."`

- [ ] **Step 1: Write failing tests:** `test_cache_miss_calls_rumik_once_then_hits` (fake `httpx.post` counts calls; two `cached("Yes, boss.")` give the same URL and one HTTP call; the second `ms` is under 50); `test_cached_returns_none_when_rumik_is_off`; `test_cache_key_changes_with_voice` (changing `rumik_speaker` gives a different path); `test_alarm_phrase` (the exact string above; with `camera_number=None` the ", camera 2" and "or show camera 2" parts are left out); `test_body_includes_description_for_mulberry` (the request JSON has `description` when set).
- [ ] **Step 2: Run** `pytest tests/test_tts_cache.py -v`; expect failures.
- [ ] **Step 3: Implement.** Write the file atomically (temp file, then rename) so two threads never serve half a WAV. Pre-render with `phrases.all_common` on a daemon thread from the lifespan; log the count and total ms.
- [ ] **Step 4: Run** `pytest -q`; all pass.
- [ ] **Step 5: Commit and push** `feat(tts): cache Friday's phrases in the Rumik voice`.

### Task 3: Voice command endpoints, Friday notices and alarm speech

**Files:**
- Create: `backend/app/services/friday.py`
- Modify: `backend/app/routers/assistant.py` (`POST /assistant/voice-command`)
- Modify: `backend/app/routers/internal.py` (`POST /internal/friday/voice-command`, `/internal/friday/event`, `/internal/friday/heartbeat`; call `friday.alarm(incident_id)` in `incident_created` for critical and high)
- Modify: `backend/app/schemas.py` (`VoiceCommandOut`, `FridayEventIn`, `FridayNotice`)
- Modify: `backend/app/config.py` (`friday_user: str = ""`, `friday_listener_timeout_s: float = 15.0`)
- Modify: `backend/app/routers/overview.py` (problem `friday_offline` when `FRIDAY_USER` is set and the listener is silent; add the kind to `OverviewProblem`)
- Test: `backend/tests/test_friday_api.py`
- Docs: `CONTRACT.md` P25 (endpoints, notice shape, `speaker` rule), `CLAUDE.md` product update 5 (assistant name Friday), `.env.example` (`FRIDAY_USER`)

**Interfaces:**
- Consumes: `tts.cached`, `phrases.*` (Task 2); `commands.handle(text, user) -> CommandResult`; `stt.transcribe(data, filename, content_type) -> tuple[str, str]`.
- Produces: `friday.heartbeat() -> None`, `friday.speaker() -> Literal["listener","dashboard"]`, `friday.notify(state: str, **fields) -> dict` (publishes `{"kind":"friday","state",…,"speaker"}` through `manager.publish_from_thread` and returns it), `friday.alarm(incident_id: int) -> dict | None`, `friday.run_voice_command(audio: bytes, filename: str, content_type: str, user: User) -> VoiceCommandOut`.
- `VoiceCommandOut = {transcript: str, intent: str, status: str, answer: str, confirm_token: str | None, audio_url: str | None, data: dict, ms: {stt: int, command: int, tts: int, total: int}}`.
- `FridayEventIn = {state: Literal["listening","thinking","idle"]}`.
- Notice states: `idle | listening | thinking | speaking | alarm`; alarm notices carry `incident_id`, `answer`, `audio_url`.

- [ ] **Step 1: Write failing tests** (fake `transcribe` and fake `tts.cached` with monkeypatch):
  - `test_voice_command_runs_the_command_and_returns_audio`: transcript "who is on duty" leads to `intent == "on_duty"`, `audio_url == "/media/tts/x.wav"`, all four `ms` keys present, and an `assistant_command` audit row with channel `voice`.
  - `test_voice_command_with_no_speech_says_sorry`: an empty transcript gives 200 with `status == "unknown"` and `answer == "Sorry, say that again."` (not a 422, so the listener can speak it).
  - `test_internal_voice_command_acts_as_friday_user`: with `FRIDAY_USER="Anita Rao"` and the right key, intent `summary` works; a wrong key gives 401; an unknown or inactive `FRIDAY_USER` gives 503 "set FRIDAY_USER".
  - `test_speaker_is_listener_while_heartbeat_is_fresh` / `test_speaker_falls_back_when_heartbeat_is_stale` (monkeypatch `time.monotonic`).
  - `test_alarm_is_broadcast_for_critical_and_high_only`: a fire incident gives one notice with `state == "alarm"`, `incident_id`, `answer` starting "Fire detected on"; a ppe incident gives none.
  - `test_alarm_goes_out_without_audio_when_rumik_fails`: `tts.cached` returns `(None, 0)`; the notice still goes out with `audio_url is None`, and escalation `start` is still called.
  - `test_friday_event_is_relayed`: `POST /internal/friday/event {"state":"listening"}` publishes a `friday` notice with `state == "listening"`.
- [ ] **Step 2: Run** `pytest tests/test_friday_api.py -v`; expect failures.
- [ ] **Step 3: Implement.** `run_voice_command`: notify thinking, transcribe, run `commands.handle` (empty text gives the SORRY answer), `tts.cached(answer)`, notify speaking with `transcript`, `answer`, `audio_url`, then log `assistant_command` (as `_log` in `assistant.py` does). The alarm text calls `phrases.alarm` with the first Head Guard contact from `contacts_on_duty` as `calling`. Screen notices from the command (`data.camera_ids`) go out as before.
- [ ] **Step 4: Run** `pytest -q`; start the API once (`uvicorn app.main:app --port 8000`), `curl /health`.
- [ ] **Step 5: Commit and push** `feat(friday): voice-command endpoints, friday notices and alarm speech`.

### Task 4: The listener's full loop

**Files:**
- Create: `backend/voice/session.py` (pure logic: recording until silence, one conversation turn)
- Create: `backend/voice/api_client.py` (httpx calls to the internal endpoints, with the key)
- Modify: `backend/voice/run_listener.py` (wire the loop; play the reply; heartbeat thread every 5 s)
- Modify: `backend/voice/wake.py` (`ensure_model` accepts a file path: no download when `WAKE_MODEL` ends in `.onnx` and exists; `on_trigger` receives the open audio queue so the session reads the same microphone)
- Modify: `backend/app/config.py` (`voice_silence_s: float = 1.0`, `voice_max_s: float = 12.0`, `voice_silence_rms: int = 500`)
- Test: `backend/tests/test_voice_session.py`

**Interfaces:**
- Consumes: the internal endpoints and `VoiceCommandOut` from Task 3.
- Produces: `record_until_silence(chunks: Iterable[np.ndarray], *, sample_rate: int, silence_s: float, max_s: float, rms_threshold: int) -> bytes` (16 kHz mono WAV bytes; empty `b""` when nothing louder than the threshold was heard); `Session(client: ApiClient, player: Callable[[str], None]).turn(audio: bytes) -> VoiceCommandOut | None` (handles `needs_confirmation` by asking `player` for the CONFIRM phrase, recording once more and calling `/assistant/confirm` through the client).
- Produces: `ApiClient(base_url: str, key: str)` with `.event(state)`, `.heartbeat()`, `.voice_command(wav: bytes) -> dict`, `.confirm(token: str, yes: bool) -> dict`, `.audio(url: str) -> bytes`.

- [ ] **Step 1: Write failing tests:** `test_record_stops_after_one_second_of_silence` (0.5 s of loud chunks then 2 s of quiet: the WAV lasts about 1.5 s, within 0.1 s); `test_record_stops_at_max_length`; `test_session_with_silence_returns_to_waiting` (all quiet: `turn` returns None and sends no voice command); `test_session_confirms_big_action_with_yes` (fake client: the first reply needs confirmation, the second recording transcribes as "yes", and `confirm(token, True)` is called once); `test_ensure_model_uses_local_file` (a tmp `hey_friday.onnx` path: no download call).
- [ ] **Step 2: Run** `pytest tests/test_voice_session.py -v`; expect failures.
- [ ] **Step 3: Implement.** Recognise yes and no from the confirm reply's transcript with the same word lists Twilio speech uses (`yes|yeah|confirm|do it` / `no|cancel|stop`). Playback: fetch the WAV with `ApiClient.audio` and play it with `sounddevice` (blocking, so the mic does not hear Friday).
- [ ] **Step 4: Run** `pytest -q`.
- [ ] **Step 5: Commit and push** `feat(voice): wake, record, send, play loop for Friday`.

### Task 5: "Hey Friday" training kit

**Files:**
- Create: `backend/tools/wake_training/README.md` (step by step: open openWakeWord's automatic training notebook in Colab, set the phrase "hey friday", the run time, download `hey_friday.onnx` into `backend/models/`, set `WAKE_MODEL=models/hey_friday.onnx`, test with `python -m voice.run_listener`)
- Create: `backend/tools/wake_training/settings.yaml` (the notebook's settings for "hey friday": target phrase, number of examples, steps; values from the notebook's defaults)
- Docs: `docs/disclosures.md` (Colab run and openWakeWord training data, with licence), `docs/TECHNICAL.md` (wake word section), `.env.example` (comment on `WAKE_MODEL`)

**Interfaces:** Consumes the file-path support from Task 4. Produces only documentation and settings.

- [ ] **Step 1:** Check the current notebook link and settings names on openWakeWord's GitHub; write the README with them (no guessed field names).
- [ ] **Step 2:** `pytest -q` (nothing breaks); commit and push `docs(wake): Hey Friday training kit`.

### Task 6: Frontend: the orb, the overlay and the red alarm glow

**Files:**
- Modify: `frontend/src/lib/types.ts` (`FridayNotice`, `VoiceCommandResponse`; add `FridayNotice` to `WsNotice`)
- Create: `frontend/src/components/friday/FridayOrb.tsx` (SVG rings plus framer-motion; props `state`, `level` 0..1)
- Create: `frontend/src/components/friday/FridayOverlay.tsx` (full screen; captions; Esc closes; plays `audio_url` when `speaker === 'dashboard'`; when `audio_url` is null it reads `answer` with the browser's `speechSynthesis`, picking an en-IN female voice if there is one)
- Create: `frontend/src/components/friday/AlarmGlow.tsx` (pulsing red border and banner until the incident is acknowledged; Acknowledge button calls `acknowledgeEvent`)
- Create: `frontend/src/lib/friday.ts` (a small store: current state, last transcript and answer, active alarm; subscribes to `wsClient`)
- Modify: `frontend/src/app/Layout.tsx` (mount the overlay and the alarm glow; unlock audio on the first click so alarms can play)
- Modify: `frontend/src/lib/ws.ts` (mock mode emits a `friday` alarm with every mock fire incident)

**Interfaces:**
- Consumes: the notice shape from Task 3 (`kind:"friday"`, `state`, `transcript?`, `answer?`, `audio_url?`, `incident_id?`, `speaker`).
- Produces: `useFriday(): {state, transcript, answer, alarm: {incidentId, text} | null, open(): void, close(): void}` in `lib/friday.ts`.

- [ ] **Step 1:** Implement the components (cyan `#22d3ee` rings for listening, a spinning outer ring for thinking, rings scaled by the audio level while speaking, red `#ef4444` for an alarm; respects `prefers-reduced-motion`).
- [ ] **Step 2: Verify:** `cd frontend; npm run build; npm run lint` (no new errors). Playwright smoke in the scratchpad (not in the repo) against `npm run dev` with mocks: log in, wait for a mock fire, expect the alarm glow, click Acknowledge, expect it gone; inject a `listening` notice and expect the orb.
- [ ] **Step 3: Commit and push** `feat(frontend): Friday orb, overlay and red alarm glow`.

### Task 7: Frontend: push-to-talk, the Friday page and show-cameras

**Files:**
- Create: `frontend/src/lib/pushToTalk.ts` (hold the spacebar outside text fields: record with `MediaRecorder` (webm), release: `voiceCommand(blob)`)
- Modify: `frontend/src/lib/api.ts` (`voiceCommand(audio: Blob): Promise<VoiceCommandResponse>` to `POST /assistant/voice-command`; mock reply in mock mode)
- Modify: `frontend/src/pages/Assistant.tsx` (the Friday page: big orb, typed box using `assistantCommand`, history list of turns)
- Modify: `frontend/src/pages/Live.tsx` (read `?cameras=a,b` and show only those; a "Show all" chip clears it)
- Modify: `frontend/src/app/Layout.tsx` (nav label "Friday"; a `friday` notice or command reply with `data.camera_ids` navigates to `/live?cameras=…`)

**Interfaces:** Consumes `useFriday` (Task 6) and `VoiceCommandOut` (Task 3).

- [ ] **Step 1:** Implement. Push-to-talk shows the overlay in `listening` while held and `thinking` after release. A typed command goes through the same history and plays the reply audio.
- [ ] **Step 2: Verify:** build and lint; Playwright smoke with mocks: type "show cameras 1 and 2" on the Friday page and expect `/live?cameras=` with 2 tiles; hold the spacebar on Live (fake media stream flag) and expect the orb.
- [ ] **Step 3: Commit and push** `feat(frontend): push-to-talk, Friday page, show cameras`.

### Task 8: End-to-end check and the docs that span tasks

**Files:**
- Modify: `docs/TECHNICAL.md` (Friday flow, settings, timings), `docs/STUDY.md` (one plain-words note each: WebSocket presence, the audio cache, recording until silence), `CLAUDE.md` (Current status), `docs/CONTRACT.md` changelog line

- [ ] **Step 1:** Full `pytest -q`; frontend build and lint.
- [ ] **Step 2:** Live run in the sandbox: API with `CALLS_MODE=simulate`, `WHATSAPP_MODE=simulate` and a fake Rumik (a local stub server returning a WAV); post a fire incident through `/internal/incidents/{id}/created`; confirm the alarm notice reaches a WebSocket client within 1 s, the Head Guard call at 0 s, and the Admin call skipped when the Admin's dashboard acknowledges within 5 s. Report the timings.
- [ ] **Step 3:** Commit and push `docs: Friday flow, settings and status`.
