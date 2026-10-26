/**
 * lib/types.ts
 * TypeScript types that mirror the API contract (docs/CONTRACT.md) exactly.
 * All times from the API are UTC ISO-8601 strings ending with 'Z'.
 */

/* ── Auth & Users ───────────────────────────────────────── */

export type UserRole = 'Admin' | 'Manager' | 'Head Guard' | 'Guard' | string

export type UserAction =
  | 'all_cameras'
  | 'ack'
  | 'resolve'
  | 'manage_users'
  | 'view_audit'
  | 'manage_zones'
  | 'manage_responders'
  | 'alert_all'
  | 'broadcast'
  | 'assistant'
  | 'manage_sites'
  | 'manage_guards'
  | 'view_guard_details'
  | 'scan_badges'
  | 'delegate_cameras'
  | string

export interface AuthUser {
  id: string
  name: string
  role: UserRole
  camera_ids?: string[]
  actions?: UserAction[]
}

export interface LoginRequest {
  pin: string
}

export interface LoginResponse {
  token: string
  user: {
    id: string
    name: string
    role: string
  }
}

export interface MeResponse {
  id: string
  name: string
  role: string
  camera_ids: string[]
  actions: string[]
}

export interface User {
  id: string
  name: string
  role: string
  camera_ids: string[]
  actions: string[]
  active: boolean
  pin?: string
}

/* ── Enums ─────────────────────────────────────────────── */

export type IncidentType =
  | 'access'
  | 'ppe'
  | 'fall'
  | 'fire'
  | 'smoke'
  | 'no_helmet'
  | 'unattended'
  | 'loitering'
  | 'crowding'

export type Severity = 'critical' | 'high' | 'low'

export type IncidentStatus =
  | 'open'
  | 'acknowledged'
  | 'resolved'
  | 'false_alarm'

export type TimelineAction =
  | 'detected'
  | 'call_placed'
  | 'call_no_answer'
  | 'escalated'
  | 'call_escalated'
  | 'whatsapp_sent'
  | 'acknowledged'
  | 'resolved'
  | 'false_alarm'
  | 'roll_call_responded'
  | 'roll_call_safe'
  | 'alert_all'
  | 'command'
  | 'note_added'
  | 'duty_fallback'
  | 'call_skipped_on_screen'
  | 'nobody_answered'
  | string

/* ── Timeline event ────────────────────────────────────── */

export interface TimelineEvent {
  at: string            // UTC ISO-8601
  action: TimelineAction
  to?: string           // guard name (call_placed / escalated)
  by?: string           // guard name (acknowledged / resolved)
  via?: 'keypad' | 'voice' | 'dashboard' | 'web' | string
  note?: string
}

/* ── Incident ──────────────────────────────────────────── */

export interface Incident {
  id: number
  type: IncidentType
  severity: Severity
  status: IncidentStatus
  camera_id: string
  camera_name: string
  zone: string
  started_at: string        // UTC ISO-8601
  confidence: number        // 0–1
  snapshot_url: string
  clip_url: string
  acknowledged_by?: string
  timeline: TimelineEvent[]
}

export interface ResolveIncidentPayload {
  outcome: 'resolved' | 'false_alarm'
  note?: string
}

/* ── Zones & Cameras ───────────────────────────────────── */

export type ZoneKind = 'restricted' | 'ppe' | 'work'

export interface Zone {
  id: string
  name: string
  kind?: ZoneKind
  polygon?: [number, number][]  // normalised 0–1 coordinates
  points?: Array<{ x: number; y: number }> // backward compatibility
  camera_id?: string
  max_people?: number | null
  color?: string
}

export type ZonePolygon = Zone

export interface Camera {
  id: string
  name: string
  stream_url: string
  online: boolean
  fps: number
  zones: Zone[]
  location?: string
  snapshot_url?: string
}

export interface CameraStreamUrlResponse {
  stream_url: string
}

/* ── Responder ─────────────────────────────────────────── */

export type Shift = 'morning' | 'afternoon' | 'night' | string
export type Language = 'en' | 'ta' | 'hi' | 'English' | 'Tamil' | 'Hindi' | 'Telugu' | 'Kannada' | 'Malayalam' | string

export interface Responder {
  id: string
  name: string
  phone: string
  language: Language
  zone: string
  shift_start?: string      // HH:MM (24-hour, IST)
  shift_end?: string        // HH:MM (24-hour, IST)
  shift?: Shift             // backward-compatibility
  call_order: number
  active: boolean
}

/* ── Health ────────────────────────────────────────────── */

export type DbHealth = 'ok' | 'error' | 'not_checked'
export type TwilioHealth = 'ok' | 'not_configured' | 'error' | 'not_checked'
export type LlmHealth = 'ok' | 'ollama_only' | 'error' | 'not_checked'

export interface CameraHealth {
  id: string
  online: boolean
  fps: number
}

export interface HealthResponse {
  cameras: CameraHealth[]
  db: DbHealth | boolean
  twilio: TwilioHealth | boolean
  llm: LlmHealth | boolean
}

/* ── Stats ─────────────────────────────────────────────── */

export interface IncidentTypeCount {
  type: IncidentType
  count: number
}

export interface DailyCount {
  date: string   // YYYY-MM-DD
  count: number
}

export interface StatsResponse {
  by_type: Record<string, number> | IncidentTypeCount[]
  by_day: DailyCount[]
  open: number
  resolved: number
  avg_response_s: number | null
  open_count?: number
  resolved_count?: number
  avg_response_seconds?: number | null
}

/* ── Query filters ─────────────────────────────────────── */

export interface EventFilters {
  type?: IncidentType
  camera?: string
  severity?: Severity
  status?: IncidentStatus
  from?: string   // ISO-8601 UTC
  to?: string     // ISO-8601 UTC
  search?: string
  limit?: number
}

/* ── Assistant ─────────────────────────────────────────── */

export interface AssistantQueryResponse {
  answer: string
  incidents: Incident[]
  source?: 'groq' | 'ollama' | 'keywords' | string
  ms?: number
}

export interface AssistantVoiceResponse {
  transcript: string
  answer: string
  incidents: Incident[]
  source?: string
  ms?: number
}

export interface AssistantCommandResponse {
  intent: string
  status: 'done' | 'needs_confirmation' | 'unknown' | 'refused' | string
  answer: string
  confirm_token?: string
  data?: Record<string, unknown>
  ms?: number
}

export interface AssistantConfirmResponse {
  status: string
  answer: string
  ms?: number
}

/* ── Zone API ──────────────────────────────────────────── */

export interface ZonePayload {
  camera_id: string
  zones: Zone[]
}

/* ── Alerts & Roll Call ────────────────────────────────── */

export interface AlertAllNotice {
  kind: 'alert_all'
  roll_call_id?: string
  incident_id?: number
  message: string
  by?: string
  at?: string
  recipients?: string[]
  whatsapp_sent?: number
  calls_queued?: number
}

export interface RollCallGuard {
  id: string
  name: string
  status: 'pending' | 'responding' | 'safe' | 'not_reached' | string
}

export interface RollCallNotice {
  kind: 'roll_call'
  roll_call_id?: string
  incident_id?: number
  message?: string
  total: number
  responding: number
  safe: number
  not_reached: number
  guards: RollCallGuard[]
}

export interface RollCallResponse {
  roll_call_id?: string
  incident_id?: number
  message?: string
  total: number
  responding: number
  safe: number
  not_reached: number
  guards: RollCallGuard[]
}

/* ── Invitations & Audit Log ───────────────────────────── */

export interface Invite {
  invite_id: string
  user_id?: string
  name: string
  email: string
  role: string
  status: string
  expires_at: string
  created_by?: string
  link: string
  qr_png?: string
  email_status?: 'sent' | 'simulated' | 'not_sent' | 'off' | string
}

export interface AuditLogRow {
  at: string
  user: string
  action: string
  target: string
  channel: 'dashboard' | 'phone' | 'voice' | string
}

export interface SectorSummary {
  from: string
  to: string
  sector: {
    all_cameras: boolean
    camera_ids: string[]
  }
  incidents: {
    total: number
    by_status: Record<string, number>
    by_type: Record<string, number>
  }
  violations: {
    restricted_entries: number
    still_open: number
    items: unknown[]
  }
  cameras: Camera[]
  logins?: AuditLogRow[]
  text: string
}

/* ── WS event ──────────────────────────────────────────── */

export type WsNotice = AlertAllNotice | RollCallNotice

export type WsEvent =
  | Incident
  | WsNotice
  | { event: 'new_incident';     data: Incident }
  | { event: 'updated_incident'; data: Incident }
