/**
 * lib/admin.ts
 * API calls and types for the admin screens: dashboard, people and roles, cameras, guards and duty, logs.
 * Shapes mirror docs/CONTRACT.md (P19 to P26). These screens always use the real API (no mock data).
 */
import { http, resolveMediaUrl } from './api'

/* ── People and roles ───────────────────────────────────── */

export interface Person {
  id: number
  staff_id: string
  name: string
  role: string
  camera_ids: string[]
  actions: string[]
  active: boolean
  phone: string | null
  zone: string | null
  shift_start: string | null
  shift_end: string | null
}

export interface PersonInput {
  name: string
  role: string
  camera_ids?: string[]
  actions?: string[]
  phone: string
  zone?: string
  shift_start?: string
  shift_end?: string
}

export interface Role {
  id: number
  name: string
  description: string | null
  camera_ids: string[]
  actions: string[]
  users: number
}

export interface InviteCreated {
  invite_id: number
  user_id: number
  staff_id: string
  name: string
  email: string
  role: string
  status: string
  expires_at: string
  link: string
  qr_png: string
  email_status: 'sent' | 'simulated' | 'not_sent' | 'off'
}

export const fetchPeople = () => http.get<Person[]>('/users')
export const updatePerson = (id: number, body: Partial<PersonInput> & { active?: boolean }) =>
  http.put<Person>(`/users/${id}`, body)
export const invitePerson = (body: PersonInput & { email: string }) => http.post<InviteCreated>('/invites', body)
export const fetchRoles = () => http.get<Role[]>('/roles')
export const createRole = (body: { name: string; description?: string; camera_ids: string[]; actions: string[] }) =>
  http.post<Role>('/roles', body)
export const updateRole = (id: number, body: Partial<{ name: string; camera_ids: string[]; actions: string[] }>) =>
  http.put<Role>(`/roles/${id}`, body)

/* ── Cameras (admin) ────────────────────────────────────── */

export interface CameraAdmin {
  id: string
  number: number | null
  name: string
  source: string
  port: number
  enabled: boolean
  area_id: number | null
  added_by: string | null
  online: boolean
  fps: number
  worker: { managed: boolean; running: boolean; pid: number | null; started_at: string | null; exit_code: number | null }
}

export interface CameraTest {
  ok: boolean
  width: number | null
  height: number | null
  ms: number
  error: string | null
}

export const fetchCamerasAdmin = () => http.get<CameraAdmin[]>('/cameras/admin')
export const testCamera = (source: string) => http.post<CameraTest>('/cameras/test', { source })
export const addCamera = (body: { name: string; source: string; number?: number; role_names: string[]; start: boolean }) =>
  http.post<CameraAdmin>('/cameras', body)
export const updateCamera = (id: string, body: Partial<{ name: string; source: string; number: number; enabled: boolean }>) =>
  http.put<CameraAdmin>(`/cameras/${id}`, body)
export const cameraWorker = (id: string, op: 'start' | 'stop' | 'restart') => http.post<CameraAdmin>(`/cameras/${id}/${op}`)
export const fetchWorkerLog = (id: string) => http.get<{ camera_id: string; lines: string[] }>(`/cameras/${id}/worker-log`)

/* ── Guards and duty ────────────────────────────────────── */

export interface DutyPerson {
  user_id: number
  staff_id: string
  name: string
  role: string
  on_duty: boolean
  since: string | null
  method: string | null
  area_id: number | null
  post: string | null
  equipment: string | null
}

export interface DutyEvent {
  user_id: number
  name: string
  action: 'in' | 'out'
  method: string
  at: string
  area_id: number | null
  by: string
}

export const fetchDutyBoard = () => http.get<{ on_duty: DutyPerson[]; off_duty: DutyPerson[] }>('/duty')
export const setDuty = (user_id: number, action: 'in' | 'out') => http.post<DutyEvent>('/duty/set', { user_id, action })
export const fetchDutyPass = (user_id: number) =>
  http.get<{ user_id: number; staff_id: string; name: string; pass_token: string; qr_png: string }>(`/duty/pass/${user_id}`)
export const fetchDutyEvents = () => http.get<DutyEvent[]>('/duty/events?limit=200')

export interface GuardProfile {
  user_id: number
  name: string
  role: string
  camera_ids: string[]
  position: string | null
  site_id: number | null
  site_name: string | null
  post: string | null
  equipment: string | null
  has_phone: boolean
}

export const fetchGuardProfile = (user_id: number) => http.get<GuardProfile>(`/guards/${user_id}/profile`)

/* ── Logs ───────────────────────────────────────────────── */

export interface AuditRow {
  at: string
  user: string
  action: string
  target: string | null
  channel: 'dashboard' | 'phone' | 'voice'
  detail: Record<string, unknown> | null
}

export interface Activity {
  from: string
  to: string
  users: {
    user_id: number | null
    user: string
    role: string | null
    actions: number
    ai_questions: number
    ai_commands: number
    camera_views: number
    cameras_viewed: string[]
    last_at: string | null
  }[]
  ai: {
    at: string
    user: string
    channel: string
    kind: 'question' | 'command'
    text: string
    answer: string
    intent: string | null
    status: string | null
    source: string | null
  }[]
  camera_views: { at: string; user: string; camera_id: string }[]
}

export const fetchAuditLog = () => http.get<AuditRow[]>('/audit-log?limit=300')
export const fetchActivity = (hours = 24) => {
  const to = new Date()
  const from = new Date(to.getTime() - hours * 3600_000)
  return http.get<Activity>(`/activity?from=${from.toISOString()}&to=${to.toISOString()}`)
}

/* ── Dashboard ──────────────────────────────────────────── */

export interface Overview {
  at: string
  cameras_total: number
  cameras_online: number
  cameras_offline: number
  workers_running: number
  cameras: { id: string; number: number | null; name: string; online: boolean; fps: number; enabled: boolean; worker_running: boolean }[]
  open_incidents: number
  open_by_severity: Record<string, number>
  problems_now: { kind: string; severity: 'critical' | 'high' | 'low'; text: string; incident_id: number | null; camera_id: string | null }[]
  past_problems: { date: string; count: number }[]
  past_total_7d: number
  guards_on_duty: string[]
  guards_off_duty: string[]
  ok: boolean
  text: string
}

export interface Usage {
  media_total_bytes: number
  snapshots: { count: number; bytes: number }
  clips: { count: number; bytes: number }
  other_media: { count: number; bytes: number }
  worker_logs: { count: number; bytes: number }
  disk_free_bytes: number
  disk_total_bytes: number
  database_bytes: number | null
  rows: Record<string, number>
  incidents_by_day: { date: string; count: number }[]
  ai_queries_by_day: { date: string; count: number }[]
}

export interface Summary {
  text: string
  incidents: { total: number; by_status: Record<string, number>; by_type: Record<string, number> }
  violations: { restricted_entries: number; still_open: number }
}

export const fetchOverview = () => http.get<Overview>('/overview')
export const fetchUsage = () => http.get<Usage>('/usage')
export const fetchSummary = () => http.get<Summary>('/summary')

export function bytes(n: number | null | undefined): string {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export const mediaUrl = resolveMediaUrl
