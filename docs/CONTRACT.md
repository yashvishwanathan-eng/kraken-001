# SafetyAI API contract

This file is the single source of truth for the API between `backend/` and `frontend/`. `CLAUDE.md` and `frontend/BRIEF.md` point here and do not copy it. If the code and this file disagree, fix one of them in the same change.

Status tags:
- **(live)**: built and working in the backend now.
- **(confirmed)**: agreed and confirmed by Kirito, not built yet.
- **(proposed)**: not yet confirmed. Do not build against it without asking.

## Conventions
- All times are stored and returned in UTC, ISO 8601 with `Z`. Night window and digest time are configured in IST (see `CLAUDE.md`).
- Bad input returns 422 with a clear message. Never 500.
- Missing or invalid token returns 401. A valid token whose role lacks the action or camera returns 403. See "Proposed clarifications" for the other error codes.
- Requests and responses are typed Pydantic models in `backend/app/schemas.py`.

## Auth
- `POST /auth/pin` returns a token. Send it on every later call as `Authorization: Bearer <token>`.
- Open without a token: `/health`, `/auth/pin`, `/twilio/*`, `/internal/*` (uses `X-Internal-Key`).
- Every other endpoint needs the Bearer token, except `/stream/*`, which uses the stream token described below, and `/ws/events`, which takes the token in the query (see proposed clarification P5).
- The token is opaque: a random string. The backend stores only its hash, with an expiry (12 hours). It is not a JWT.
- The token resolves to a user with a role, `camera_ids[]` and `actions[]`.
- Permissions are data, not code: the role is a label, and the camera list and action list decide what the user can see and do.
- Demo seed roles: Admin, Manager, Head Guard, Guard. Every user sees only their delegated cameras and the incidents from those cameras, except Admin (sees everything).
- The Manager role can be delegated to a zone. "Zone manager" means the Manager role delegated to that zone.
- The audit log is visible to Admin and Manager only.
- The PIN is write-only. No endpoint ever returns it.

### Stream tokens (confirmed)
- The browser `<img>` tag cannot send a header, so streams use a signed query token.
- `GET /cameras` returns each camera's `stream_url` already signed: `/stream/{id}?st=<token>`.
- The token is short-lived (1 hour), scoped to that one camera, and checked against the user's delegated cameras.
- The frontend uses `stream_url` as is in the `<img>` tag. It does not build the URL itself.
- When the token expires, the frontend calls `GET /cameras/{id}/stream-url` for a fresh one.
- An invalid or expired `st` returns 401. A camera not delegated to the user returns 403.

## Endpoints

| Method | Path | Status | Request | Response / notes |
|---|---|---|---|---|
| POST | /invites | live (P19) | `{name, email, role, camera_ids[], actions[]}` (manage_users) | 201 `{invite_id, user_id, name, email, role, status, expires_at, created_by, link, qr_png, email_status}`. The account is created inactive; `email_status` is sent, simulated, not_sent or off. 409 if a pending invite exists for that email. |
| GET | /invites | live (P19) | | list of invitations (manage_users) |
| POST | /invites/{invite_id}/revoke | live (P19) | | cancels a pending invitation; 409 otherwise |
| GET | /invites/link/{token} | live (P19) | | public: `{name, email, role, expires_at}`; 404 if unknown, used, expired or revoked |
| POST | /invites/link/{token}/accept | live (P19) | `{pin}` | public: sets the PIN, activates the account, returns `{token, user}` (same as login). One use. 409 if the PIN is taken. |
| GET | /summary | live (P20) | query `from`, `to` (optional; default today from local midnight IST) | `{from, to, sector:{all_cameras, camera_ids[]}, incidents:{total, by_status, by_type}, violations:{restricted_entries, still_open, items[]}, cameras[], logins[], text}`. `logins` only for users with view_audit. 422 if `to` is not after `from`. |
| GET | /health | live | | `{cameras:[{id,online,fps}], db, twilio, llm}`. `db`: ok, error, not_checked. `twilio`: ok, not_configured, error, not_checked. `llm`: ok, ollama_only, error, not_checked. |
| POST | /auth/pin | live | `{pin}` | `{token, user:{id,name,role}}` |
| GET | /me | live | | `{id, name, role, camera_ids[], actions[]}` |
| GET, POST, PUT, DELETE | /users | live | `{id,name,role,camera_ids[],actions[],active,pin}`; `pin` is write-only | Admin only. Response never includes `pin`. |
| GET | /cameras | live | | `[{id,name,stream_url,online,fps,zones:[{id,name,kind,polygon:[[x,y],...]}]}]`. `stream_url` is signed (see Stream tokens). `kind`: restricted, ppe, work. Filtered by the caller's `camera_ids`. |
| GET | /cameras/{id}/stream-url | live | | `{stream_url}`: a fresh signed URL for that camera, same format and rules as `/cameras`. 403 if not delegated. |
| GET | /stream/{camera_id}?st=<token> | live | Query: `st` (stream token) | MJPEG, `multipart/x-mixed-replace`. See Stream tokens. |
| GET | /events | live | Query: type, camera, severity, status, from, to (ISO UTC) | List of incident objects, newest first. Filtered by the caller's cameras. |
| GET | /events/{id} | live | | Incident object with `timeline`. |
| POST | /events/{id}/ack | live | No body. The user comes from the token. | Changed from body `{by}`. Phone acknowledgements use the responder linked to the call. |
| POST | /events/{id}/resolve | live | `{outcome: "resolved" \| "false_alarm", note}` | Critical incidents cannot be resolved by voice or keypad. Dashboard only. |
| WS | /ws/events | live | | Sends full incident objects on create and update. Extra notices `alert_all` and `roll_call` are live; shape in proposed clarification P11. |
| GET, POST, PUT | /zones | live | Polygons per camera | Response shape as in `/cameras` zones. |
| GET, POST, PUT, DELETE | /responders | live | `{id,name,phone,language,zone,shift_start,shift_end,call_order,active}` | Responder role and camera group come from `/users`. |
| POST | /broadcast | live | multipart: `zone`, `audio` file | See proposed clarification P14. |
| GET | /roll-call | live | Query: `incident_id` | `{total, responding, safe, not_reached, guards:[{id,name,status}]}`. `status`: pending, responding, safe, not_reached. |
| POST | /alerts/all | live | `{message}` | WhatsApp to all on-shift guards, red banner on every dashboard, parallel calls (about 1 new call per second, Twilio default). |
| POST | /assistant/query | live | `{question}` | `{answer, incidents[], source:"groq" \| "ollama", ms}` |
| POST | /assistant/voice | live | multipart audio | `{transcript, answer, incidents[], source, ms}` |
| POST | /assistant/command | live | `{text}` | `{intent, status, answer, confirm_token, ms}`. `status`: done, needs_confirmation, unknown. |
| POST | /assistant/confirm | live | `{confirm_token, confirmed}` | `{status, answer, ms}`. Only confirms the big actions: call everyone, evacuate, call fire station. |
| GET | /audit-log | live | Query: user, action, channel (dashboard, phone, voice), from, to | Rows `{at, user, action, target, channel}`, newest first. Admin and Manager only. |
| GET | /stats | live | | `{by_type:{...}, by_day:[{date,count}], open, resolved, avg_response_s}` |
| POST | /twilio/voice, /twilio/gather, /twilio/status | live | Twilio webhooks | Twilio only. TwiML responses. |
| POST | /internal/incidents/{id}/created | live | Header `X-Internal-Key` | Worker to API only. |
| POST | /internal/heartbeat | live | Header `X-Internal-Key`; `{camera_id, fps, online, ts}` | Worker to API only, every 5 s. Silence over 30 s = offline in `/health` and `/cameras`. The admin WhatsApp on silence comes with WhatsApp (22 Oct). |

## Incident object

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

## Enums

- `type`: access, ppe, fall, fire, unattended, loitering, crowding
- `severity`: critical, high, low
- `status`: open, acknowledged, resolved, false_alarm
- `channel`: dashboard, phone, voice
- Timeline `action`:
  - **(live)**: detected, call_placed, call_no_answer, escalated, whatsapp_sent, acknowledged, resolved, false_alarm, roll_call_responded, roll_call_safe, alert_all
  - **(confirmed, not used yet)**: command
- Timeline `via` (on acknowledged): keypad, voice, dashboard

## DB indexes
incidents: started_at, type, camera_id, status.

## Proposed clarifications (21 Oct, need Kirito's and the frontend's sign-off)
Gate 2 needed these details, which the contract did not settle. The backend does this today; each can still change.

- **P1. Item paths.** PUT and DELETE take the id in the path: `/users/{id}`, `/zones/{id}`, `/responders/{id}`. `PUT /users/{id}` is partial: send only the fields that change. Sending `pin` resets it and logs that user out everywhere.
- **P2. Zones.** Polygon points are fractions of the frame (x and y from 0 to 1), so a zone works at any camera resolution. Zone objects (in `/zones` and in `/cameras`) also carry `camera_id` and `max_people` (crowding limit, or null). A polygon needs 3 to 50 points. Workers pick up zone edits within 30 s.
- **P3. Lists.** `/events` takes `limit` (default 100, max 500). `/audit-log` takes `limit` (default 200, max 1000); its `target` is a string like `incident:12` or `user:4`.
- **P4. Stats.** `from`/`to` set the window (default the last 7 days). `by_day` dates are IST calendar days. `open` counts status open or acknowledged; `resolved` counts resolved or false_alarm. `avg_response_s` is the average time from detection to the first acknowledgement, over acknowledged incidents; null if none.
- **P5. WebSocket auth.** Browsers cannot set headers on a WebSocket, so connect to `/ws/events?token=<token>`. A bad or missing token closes the socket with code 4401. Incident messages are the plain incident object, unchanged.
- **P6. Error codes.** 403: action or camera not allowed. 404: an incident on a camera you cannot see (its existence is not revealed). 409: acknowledging or resolving a closed incident, or a PIN already used by another user. 429: five wrong PINs within a minute from one client. 400: an admin deactivating or deleting themselves, or removing their own `manage_users`. 503: stream requested while that camera's worker is not running.
- **P7. PINs.** 4 to 8 digits. Unique across users, because login is by PIN alone.
- **P8. Actions.** The values `actions[]` can hold: `all_cameras`, `ack`, `resolve`, `manage_users`, `view_audit`, `manage_zones`, `manage_responders`, `alert_all`, `broadcast`, `assistant`.
- **P9. /me.** For a user with `all_cameras`, `camera_ids` lists every camera.
- **P10. Responders.** `phone` in E.164 (`+919876543210`). `language`: en, ta, hi. `shift_start` and `shift_end` as `HH:MM` (24-hour, IST). Any logged-in user can read; changes need `manage_responders`.
- **P11. WebSocket notices.** Besides incident objects (which have `camera_id`), `/ws/events` sends notices with a `kind` key, to every logged-in dashboard:
  - `{"kind":"alert_all","roll_call_id","incident_id","message","by","at","recipients","whatsapp_sent","calls_queued"}`: show the red banner.
  - `{"kind":"roll_call","roll_call_id","incident_id","message","total","responding","safe","not_reached","guards":[{id,name,status}]}`: refresh the roll-call board.
- **P12. Alert everybody.** `POST /alerts/all` body `{message, incident_id?}` (incident optional). Response: the `alert_all` notice above. 404 if `incident_id` does not exist.
- **P13. Roll call.** `GET /roll-call` with `incident_id` gives that incident's latest roll call; without it, the latest roll call. 404 if none. The response also carries `roll_call_id`, `incident_id`, `message`; `guards[].id` is the responder id. A guard who hangs up without pressing 1 or 2 counts as `not_reached`.
- **P14. Broadcast.** `POST /broadcast` multipart `zone` + `audio` (MP3 or WAV, up to 5 MB). Calls every on-shift responder whose zone matches and plays the audio. Response `{zone, recipients[], queued, calls_mode}`.
- **P15. Assistant.** `source` can also be `keywords` (no language model available; the built-in keyword parser answered). `/assistant/command` and `/assistant/confirm` responses add `data` (e.g. `{"camera_id"}` for "show camera N", `{"incident_ids"}` for a question, `{"roll_call_id"}` after alert everybody). Command `status` can also be `refused` (not allowed, or nothing to act on). `/assistant/confirm` answers 404 when the token is unknown, expired (60 s), already used, or belongs to another user. `/assistant/voice` answers 422 for non-audio or silence, 503 when no speech-to-text is available.
- **P16. Share links.** `GET /share/media/{name}?st=...` serves one snapshot or broadcast file to WhatsApp or Twilio (signed, 24 h). The dashboard does not use it.
- **P17. Public address.** When the API is reached through `PUBLIC_BASE_URL` (ngrok), only `/twilio/*` and `/share/*` answer; everything else is 404 there. The dashboard always uses the local address.
- **P18. Health.** `twilio` is `ok` when the Twilio keys and public URL are set, else `not_configured`. `llm` is `ok` when Groq is set up, `ollama_only` when only Ollama is, `not_checked` when neither is.

- **P19. Invitations.** Admins invite people by email (`POST /invites`). The email has a link, and the response has the same link as a QR code (`qr_png`, a PNG data URL) for a laptop screen or a phone camera. An invitation link works once, for 48 h, and only from the address in `DASHBOARD_URL`. Accepting it sets the PIN and logs the person in. No SMS is sent. Offline, the QR code is enough: scanning it opens the accept page on the same network. `email_status` tells the dashboard whether the email left (sent), was logged only (simulated), failed (not_sent) or was switched off (off), so the admin can share the link by hand.
- **P20. Sector summaries.** `GET /summary` covers the cameras the caller may see: a Head Guard sees their own cameras, an Admin (`all_cameras`) sees the overview of every camera. `violations` are restricted-area entries (access incidents); with none, `text` says "No violations". `text` is built from the counts only, so the dashboard can read it aloud. Login logs appear only for users with `view_audit`.

- **P21. Sites, guards, badges, delegation.** Sites hold buildings, buildings hold rooms or areas, and a camera can be placed in an area (`PUT /cameras/{id}/area`). `GET /sites` returns the tree with each area's cameras. Guard profiles (`GET /guards`, `GET|PUT /guards/{user_id}/profile`) hold position, post, site, equipment and whether the guard has a phone. Personal details (badge number, badge ID, security number, emergency contact) are encrypted at rest. Only the admin (`view_guard_details`) can read them; saving replaces them entirely. The list shows names and posts only, to people who share a camera with the caller. A badge read (`POST /badges/scan`, `scan_badges`) finds the guard by the badge number, which is stored as a keyed hash. `GET /presence?at=` lists who was present where, by names, for areas in the caller's cameras. A restricted-area entry with no badge scan in its area is marked `not_scanned`: the alert text says so and the sector summary counts it. Delegation: a user with `delegate_cameras` (and no `manage_users`) can create or edit people only with cameras they can see and actions they hold. New actions: `manage_sites`, `manage_guards`, `view_guard_details`, `scan_badges`, `delegate_cameras`. Needs `DATA_KEY` in `.env`; saving personal details without it returns 503.

## Changelog
- 25 Oct: P21 added: sites and areas, guard profiles with encrypted personal details, badge scans, presence, and manager delegation. Endpoints are live.
- 25 Oct: P19 (invitations by email, with a link and a QR code) and P20 (sector summaries) are live on the backend for the frontend to build. P19 and P20 are agreed by Kirito; the frontend builds the screens.
- 18 Oct: added the endpoints above. `POST /events/{id}/ack` no longer takes `by`.
- 18 Oct: confirmed by Kirito: the nine endpoints, the timeline actions, the extra WebSocket messages, and the signed stream URL with `GET /cameras/{id}/stream-url`. Stream tokens are 1 hour, one camera each.
- 21 Oct (later): escalation, Twilio webhooks, alert everybody, roll call, broadcast and the assistant are live. Proposed clarifications P11 to P18 added for sign-off.
- 21 Oct: status tags now mean built (live) vs agreed (confirmed). Gate 2 endpoints are live. Tokens are opaque (hashed in the database, 12 h). 403 is in use. Proposed clarifications P1 to P10 added for sign-off.
