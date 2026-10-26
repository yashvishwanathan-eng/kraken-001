# Friday: the voice assistant (design)

Date: 26 Oct 2026. Status: waiting for Kirito's approval. Nothing here is built yet.

## 1. What we agreed

**Kirito said**
- The assistant is called **Friday** and speaks with a female **Rumik Silk** voice. The product name stays RingGuard.
- Saying **"Hey Friday"** opens the software with a glowing circle, like the reference video (our own drawing, not the clip).
- Friday listens to what you say after the wake word, answers out loud, and does it straight away.
- During a fire or alarm: a **red glow** appears in the software and Friday **speaks** what happened. She **informs and advises; the human decides**.
- People who are **not on the software get a phone call** ("There is a fire at ..."). People who are on it get the screen and voice first (option A below).
- Friday repeats back what she understood, then acts. It must feel **instant**.
- We own the frontend and the backend now.

**Assumed (correct me)**
- "Listens to everything" means: after the wake word, until you stop talking. The room is never recorded all day.
- Existing safety rules stay: evacuate, call everyone and call the fire station need a spoken "yes"; nothing ever calls 112 or the fire station by itself; Friday acts with the permissions of one user.
- Friday's voice plays from the control-room laptop's speaker.

**Success looks like**
- Say "Hey Friday", and within 0.5 s you hear "Yes, boss" and the orb opens on screen.
- "What's the summary for cameras 1 and 2 for the past hour?": the answer starts within about 3 s (online).
- A fire on camera 2: the red glow appears within 1 s, Friday speaks within about 1 s, and the Head Guard's phone rings within 15 s.
- The Admin on the dashboard who says "acknowledge" never gets a phone call. An Admin away from the laptop gets called at once.

## 2. How it fits together

```
 laptop microphone                 API (FastAPI)                    dashboard (React)
 ─────────────────                 ──────────────                   ─────────────────
 voice/run_listener.py             POST /internal/friday/event ──►  /ws/events  {"kind":"friday", state}
   1 wake word "Hey Friday"  ───►  (wake, listening, thinking)       ─► Friday overlay: orb + captions
   2 plays "Yes, boss" (cached)
   3 records until 1 s silence
   4 POST /assistant/voice-command ─► speech to text ─► command ─► reply text ─► Rumik audio (cache)
   5 plays the reply audio  ◄──────  {transcript, intent, status, answer, audio_url, data}
                                     notices: show cameras ────────► dashboard switches view
 incident created (worker) ───────► escalation + Friday alarm ─────► red glow + Friday speaks
```

Two ways in, same brain:
- **Wake word** (`voice/run_listener.py`, Python, offline wake detection): plays audio on the laptop speaker, so there are no browser autoplay problems.
- **Push-to-talk in the browser** (spacebar on any page): records in the browser, plays the reply in the browser.

## 3. Backend changes

### 3.1 `POST /assistant/voice-command` (new, contract P25)
- Multipart audio (same limits as `/assistant/voice`). Steps: speech to text, then the existing `commands.handle` (fast patterns first; Groq only for unknown wording), then reply audio.
- Response `{transcript, intent, status, answer, confirm_token, audio_url, data, ms:{stt, command, tts, total}}`. `audio_url` is a cached WAV under `/media/tts/`, or null when Rumik is not available (the caller then shows the text, and the browser may use its own voice).
- Questions that are not commands ("what happened at the door today?") still go to the existing question path through the `query` intent.
- Logged as `assistant_command` with channel `voice` (already shown in `/activity`).

### 3.2 Friday's voice: `services/tts.py` grows a cache
- Key = hash of (model, speaker, description, text); file `media/tts/<hash>.wav`. A cache hit is instant.
- At API start (in the background), pre-render the phrases Friday says most: "Yes, boss", "Sorry, say that again", the alarm sentences for every incident type and camera ("Fire detected on Shop Floor, camera 2"), "Calling <name>" for each responder, "Say yes to confirm". Re-rendered when cameras or responders change.
- New settings: `RUMIK_MODEL=mulberry`, `RUMIK_SPEAKER`, `RUMIK_DESCRIPTION` (the female voice), `FRIDAY_NAME=Friday`.
- Uncached sentences use the HTTP API (whole WAV). If measured time to audio is over 1.5 s on the laptop, step 2 switches long answers to Rumik's streaming WebSocket (audio starts while she is still speaking). Measured first, built only if needed.

### 3.3 Friday notices on `/ws/events` (contract P25)
`{"kind":"friday", "state": "idle"|"listening"|"thinking"|"speaking"|"alarm", "transcript"?, "answer"?, "audio_url"?, "incident_id"?, "speaker": "listener"|"dashboard"}`.
- The listener tells the API about wake, listening and thinking through `POST /internal/friday/event` (`X-Internal-Key`).
- `speaker` says who plays the sound, so it is never heard twice: `listener` when the wake-word listener has sent a heartbeat in the last 15 s, otherwise `dashboard`.

### 3.4 Alarm: Friday speaks, the screen glows
- When a critical or high incident is created, the API sends `{"kind":"friday","state":"alarm"}` with the spoken text and cached audio: *"Fire detected on Shop Floor, camera 2. Calling Ravi. Say evacuate to alert everyone, or show camera 2."*
- Friday advises; she does not act. Evacuate stays behind the spoken "yes".

### 3.5 Calls depend on who is on the software (option A; changes the confirmed ladder)
- The WebSocket manager remembers which user each dashboard belongs to, so the API knows who is "on the software" right now.
- For the **Admin and Manager call steps**:
  - **Not on the software:** the call is placed at 0 s (today: Admin at 60 s, Manager at 120 s for critical).
  - **On the software:** red glow and voice first, then a grace period of `ONSCREEN_GRACE_S=20`. Acknowledging on screen, by voice or by keypad stops the ladder, so the call is never placed. No reaction: the call goes out after 20 s.
- The Head Guard call at 0 s and every WhatsApp stay exactly as they are. "Never call above Admin" and the high-severity rule (Admin gets WhatsApp only) stay.
- The call script says: *"RingGuard alert. Fire detected at Shop Floor ... Press 1 if you are going."* (already the alert text).

### 3.6 Wake word "Hey Friday" (option A)
- `tools/wake_training/`: a ready Colab notebook setup (openWakeWord automatic training, synthetic voices, about an hour) and a README. Kirito presses Run and downloads `hey_friday.onnx` into `backend/models/`; `WAKE_MODEL=models/hey_friday.onnx`.
- Until then, "Hey Jarvis" stays the stand-in. Colab use gets a line in `disclosures.md`.

### 3.7 The listener's full loop (`voice/run_listener.py`)
wake, then the cached "Yes, boss", then record until 1 s of silence (at most 12 s), then `/assistant/voice-command`, then play the reply. If the reply is `needs_confirmation`, it listens once more for yes or no and calls `/assistant/confirm`. It sends to `POST /internal/friday/voice-command` (same body and response as 3.1, header `X-Internal-Key`), which acts as one user named in `.env` (`FRIDAY_USER`, the control-room admin), so that user's permissions still apply. It sends `POST /internal/friday/heartbeat` every 5 s.

## 4. Frontend changes

- **Friday overlay** (on every page, in `Layout.tsx`): a full-screen glowing orb (SVG + framer-motion) driven by `kind:"friday"` notices. States: idle (hidden), listening (cyan pulse), thinking (spinning ring), speaking (rings follow the audio level), alarm (red glow around the whole screen, the orb turns red). Captions show what you said and Friday's answer. Esc closes it.
- **Alarm glow:** red pulsing border and banner on every page until the incident is acknowledged. A button and the voice command "acknowledge" both clear it.
- **Push-to-talk:** hold the spacebar on any page (not inside text boxes) to record; release to send. The same overlay shows.
- **Assistant page:** becomes Friday's page: the orb, a typed box, and the history of questions and answers.
- **Screen actions:** "show cameras 3, 4 and 12" opens Live with only those cameras.
- **Audio:** plays `audio_url` when `speaker` is `dashboard`; otherwise only animates.

## 5. Contract changes (P25)
`POST /assistant/voice-command`, `POST /internal/friday/voice-command`, `POST /internal/friday/event`, `POST /internal/friday/heartbeat`, the `friday` WebSocket notice, the on-screen grace in the ladder, and the timeline action `call_skipped_on_screen` (written when an on-screen acknowledgement stops a call before it is placed).

## 6. Errors and fallbacks
- Rumik down or slow (over 8 s): text captions plus the browser's own voice; the alarm still glows and the calls still go out.
- Speech to text down: "Sorry, I can't hear you right now. Use the keyboard." Typing still works.
- Unknown order: "Sorry, say that again", plus two example commands.
- The listener crashes: push-to-talk in the browser still works; `/overview` shows "Friday listener offline".
- Nobody on the software and nobody answers: the existing ladder goes on unchanged.

## 7. Testing
- Backend (pytest): voice-command with a fake STT and fake Rumik (transcript, intent, cached audio); TTS cache hit and miss; `speaker` choice; the grace period (on screen and acknowledged: no call; on screen without reaction: call at 20 s; not on screen: call at 0 s); the friday notice shapes; the listener loop with fake audio and fake HTTP.
- Frontend: `npm run build` and lint; a Playwright smoke test against the mock API (orb opens on a friday notice, alarm glow appears and clears on ack, show-cameras switches the Live page).
- Live on the laptop: real mic, real Rumik key, timing printed for each step.

## 8. Build order
1. Backend: voice-command, TTS cache, friday notices, alarm speech, on-screen grace (with tests).
2. Listener loop (wake, record, send, play, confirm).
3. Frontend: overlay and orb, alarm glow, push-to-talk, Friday page, show-cameras.
4. Wake word training kit and docs (`CONTRACT.md` P25, `TECHNICAL.md`, `disclosures.md`, `CLAUDE.md`).

## 9. Out of scope (for now)
Recording the room all the time; Friday acting by herself on a timer; Tamil or Hindi replies; Rumik streaming unless step 1 measurements need it.
