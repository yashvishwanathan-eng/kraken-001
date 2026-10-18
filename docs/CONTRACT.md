# SafetyAI API contract

This file is the single source of truth for the API between `backend/` and `frontend/`. `CLAUDE.md` and `frontend/BRIEF.md` point here and do not copy it. If the code and this file disagree, fix one of them in the same change.

Status tags: **(live)** = in the backend now. **(proposed)** = agreed in the 18 Oct review, not yet built or not yet confirmed by the frontend.

## Conventions
- All times are stored and returned in UTC, ISO 8601 with `Z`. Night window and digest time are configured in IST (see `CLAUDE.md`).
- Bad input returns 422 with a clear message. Never 500.
- Missing or invalid token returns 401. A valid token whose role lacks the action returns 403 (proposed).
- Requests and responses are typed Pydantic models in `backend/app/schemas.py`.

## Auth
- `POST /auth/pin` returns a token. Send it on every later call as `Authorization: Bearer <token>`.
- Open without a token: `/health`, `/auth/pin`, `/twilio/*`, `/internal/*` (uses `X-Internal-Key`), `/stream/*` (proposed: stream requires a token; see note below).
- Every other endpoint needs a token. The token resolves to a user with a role, `camera_ids[]` and `actions[]`.
- Permissions are data, not code: the role is a label, and the camera list and action list decide what the user can see and do.
- Demo seed roles: Admin, Manager, Head Guard, Guard. Every user sees only their delegated cameras and the incidents from those cameras, except Admin (sees everything).
- The audit log is visible to Admin and Manager only.
- The PIN is write-only. No endpoint ever returns it.

Note on `/stream/*`: the MJPEG proxy is reached from a browser `<img>` tag, which cannot send a header. Decide between a short-lived stream token in the query string or a cookie. Still open.

## Endpoints

| Method | Path | Status | Request | Response / notes |
|---|---|---|---|---|
| GET | /health | live | | `{cameras:[{id,online,fps}], db, twilio, llm}`. `db`: ok, error, not_checked. `twilio`: ok, not_configured, error, not_checked. `llm`: ok, ollama_only, error, not_checked. |
| POST | /auth/pin | proposed | `{pin}` | `{token, user:{id,name,role}}` |
| GET | /me | proposed | | `{id, name, role, camera_ids[], actions[]}` |
| GET, POST, PUT, DELETE | /users | proposed | `{id,name,role,camera_ids[],actions[],active,pin}`; `pin` is write-only | Admin only. Response never includes `pin`. |
| GET | /cameras | live | | `[{id,name,stream_url,online,fps,zones:[{id,name,kind,polygon:[[x,y],...]}]}]`. `kind`: restricted, ppe, work. Filtered by the caller's `camera_ids`. |
| GET | /stream/{camera_id} | live | | MJPEG, `multipart/x-mixed-replace`. See auth note. |
| GET | /events | live | Query: type, camera, severity, status, from, to (ISO UTC) | List of incident objects, newest first. Filtered by the caller's cameras. |
| GET | /events/{id} | live | | Incident object with `timeline`. |
| POST | /events/{id}/ack | live, changed | No body. The user comes from the token. | Changed from body `{by}`. Phone acknowledgements use the responder linked to the call. |
| POST | /events/{id}/resolve | live | `{outcome: "resolved" \| "false_alarm", note}` | Critical incidents cannot be resolved by voice or keypad. Dashboard only. |
| WS | /ws/events | live | | Sends full incident objects on create and update. Proposed extra messages: `alert_all` and roll-call updates (shape TBD). |
| GET, POST, PUT | /zones | live | Polygons per camera | Response shape as in `/cameras` zones. |
| GET, POST, PUT, DELETE | /responders | live | `{id,name,phone,language,zone,shift_start,shift_end,call_order,active}` | Responder role and camera group come from `/users`. |
| POST | /broadcast | live | multipart: `zone`, `audio` file | Response shape not yet defined. |
| GET | /roll-call | proposed | Query: `incident_id` | `{total, responding, safe, not_reached, guards:[{id,name,status}]}`. `status`: pending, responding, safe, not_reached. |
| POST | /alerts/all | proposed | `{message}` | WhatsApp to all on-shift guards, red banner on every dashboard, parallel calls (about 1 new call per second, Twilio default). |
| POST | /assistant/query | live | `{question}` | `{answer, incidents[], source:"groq" \| "ollama", ms}` |
| POST | /assistant/voice | live | multipart audio | `{transcript, answer, incidents[], source, ms}` |
| POST | /assistant/command | proposed | `{text}` | `{intent, status, answer, confirm_token, ms}`. `status`: done, needs_confirmation, unknown. |
| POST | /assistant/confirm | proposed | `{confirm_token, confirmed}` | `{status, answer, ms}`. Only confirms big actions (see `CLAUDE.md`). |
| GET | /audit-log | proposed | Query: user, action, channel (dashboard, phone, voice), from, to | Rows `{at, user, action, target, channel}`, newest first. Admin and Manager only. |
| GET | /stats | live | | `{by_type:{...}, by_day:[{date,count}], open, resolved, avg_response_s}` |
| POST | /twilio/voice, /twilio/gather, /twilio/status | live | Twilio webhooks | Twilio only. TwiML responses. |
| POST | /internal/incidents/{id}/created | live | Header `X-Internal-Key` | Worker to API only. |
| POST | /internal/heartbeat | proposed | Header `X-Internal-Key`; `{camera_id, fps, online, ts}` | Worker to API only. Silence over 30 s triggers an admin WhatsApp. |

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
  - **(live)**: detected, call_placed, call_no_answer, acknowledged, escalated, whatsapp_sent, resolved, false_alarm
  - **(proposed)**: roll_call_responded, roll_call_safe, alert_all, command
- Timeline `via` (on acknowledged): keypad, voice, dashboard (proposed: keep in sync with channel)

## DB indexes
incidents: started_at, type, camera_id, status.

## Changelog
- 18 Oct: added the proposed endpoints above. `POST /events/{id}/ack` no longer takes `by`. Proposed timeline actions and extra WebSocket messages need frontend sign-off.
