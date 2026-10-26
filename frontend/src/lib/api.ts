/**
 * lib/api.ts
 * Real backend API client for RingGuard, adhering strictly to docs/CONTRACT.md.
 * When VITE_USE_MOCKS=true, mock data is used for testing and offline development.
 */

import type {
  Camera,
  CameraStreamUrlResponse,
  EventFilters,
  HealthResponse,
  Incident,
  LoginResponse,
  MeResponse,
  Responder,
  StatsResponse,
  AssistantQueryResponse,
  AssistantVoiceResponse,
  AssistantCommandResponse,
  AssistantConfirmResponse,
  ZonePayload,
  AlertAllNotice,
  RollCallResponse,
  AuditLogRow,
  SectorSummary,
  AuthUser,
} from './types'

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'
const API_URL   = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '')

export const STORAGE_KEY_TOKEN = 'ringguard_token'
export const STORAGE_KEY_USER  = 'ringguard_user'

/* ── Auth Session Helpers ───────────────────────────────── */

export function getAuthToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY_TOKEN)
  } catch {
    return null
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_USER)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function setAuthSession(token: string, user: AuthUser): void {
  try {
    localStorage.setItem(STORAGE_KEY_TOKEN, token)
    localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user))
  } catch {
    // ignore local storage errors
  }
}

export function clearAuthSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY_TOKEN)
    localStorage.removeItem(STORAGE_KEY_USER)
  } catch {
    // ignore
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('auth:unauthorized'))
  }
}

/* ── Media & Stream URL Resolution ──────────────────────── */

/**
 * Prepends VITE_API_URL to relative media and stream URLs (e.g. /media/128.jpg, /stream/cam1?st=...).
 */
export function resolveMediaUrl(url?: string | null): string {
  if (!url) return ''
  if (
    url.startsWith('http://') ||
    url.startsWith('https://') ||
    url.startsWith('blob:') ||
    url.startsWith('data:')
  ) {
    return url
  }
  const cleanPath = url.startsWith('/') ? url : `/${url}`
  return `${API_URL}${cleanPath}`
}

/* ── Request Helpers ────────────────────────────────────── */

function authHeaders(customHeaders: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...customHeaders }
  const token = getAuthToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

async function handleResponse<T>(res: Response, path: string): Promise<T> {
  if (res.status === 401) {
    clearAuthSession()
    throw new Error(`Unauthorized (401) on ${path}`)
  }
  if (!res.ok) {
    let errorDetail = ''
    try {
      const errJson = await res.json()
      errorDetail = errJson.detail || errJson.message || JSON.stringify(errJson)
    } catch {
      // not JSON
    }
    throw new Error(`${res.status} ${res.statusText}${errorDetail ? `: ${errorDetail}` : ''}`)
  }
  if (res.status === 204) {
    return undefined as unknown as T
  }
  return res.json() as Promise<T>
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: authHeaders(),
  })
  return handleResponse<T>(res, `GET ${path}`)
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const headers = authHeaders({ 'Content-Type': 'application/json' })
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return handleResponse<T>(res, `POST ${path}`)
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const headers = authHeaders({ 'Content-Type': 'application/json' })
  const res = await fetch(`${API_URL}${path}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(body),
  })
  return handleResponse<T>(res, `PUT ${path}`)
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  return handleResponse<T>(res, `DELETE ${path}`)
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  return handleResponse<T>(res, `POST ${path}`)
}

/* ── Mock Loaders ───────────────────────────────────────── */

async function mockIncidents(): Promise<Incident[]> {
  const mod = await import('../mocks/incidents.json')
  return (mod.default as Incident[]).map(normalizeIncident)
}

async function mockCameras(): Promise<Camera[]> {
  const mod = await import('../mocks/cameras.json')
  return (mod.default as Camera[]).map(normalizeCamera)
}

async function mockResponders(): Promise<Responder[]> {
  const mod = await import('../mocks/responders.json')
  return mod.default as Responder[]
}

async function mockStats(): Promise<StatsResponse> {
  const mod = await import('../mocks/stats.json')
  const raw = mod.default as unknown as {
    by_type: unknown
    by_day: unknown
    open_count?: number
    resolved_count?: number
    avg_response_seconds?: number
    open?: number
    resolved?: number
    avg_response_s?: number
  }
  return {
    by_type: raw.by_type as Record<string, number>,
    by_day: (raw.by_day as Array<{ date: string; count: number }>) || [],
    open: raw.open ?? raw.open_count ?? 8,
    resolved: raw.resolved ?? raw.resolved_count ?? 17,
    avg_response_s: raw.avg_response_s ?? raw.avg_response_seconds ?? 94,
    open_count: raw.open_count ?? raw.open ?? 8,
    resolved_count: raw.resolved_count ?? raw.resolved ?? 17,
    avg_response_seconds: raw.avg_response_seconds ?? raw.avg_response_s ?? 94,
  }
}

/* ── Incident / Camera Normalizers ──────────────────────── */

function normalizeIncident(i: Incident): Incident {
  return {
    ...i,
    snapshot_url: resolveMediaUrl(i.snapshot_url),
    clip_url: resolveMediaUrl(i.clip_url),
  }
}

function normalizeCamera(c: Camera): Camera {
  return {
    ...c,
    stream_url: resolveMediaUrl(c.stream_url),
    snapshot_url: c.snapshot_url ? resolveMediaUrl(c.snapshot_url) : undefined,
  }
}

/* ── Auth API ───────────────────────────────────────────── */

/**
 * Authenticates user via PIN.
 * In mock mode (VITE_USE_MOCKS=true), any PIN logs in as Admin.
 */
export async function loginWithPin(pin: string): Promise<LoginResponse> {
  if (USE_MOCKS) {
    const mockRes: LoginResponse = {
      token: `mock-token-${Date.now()}`,
      user: {
        id: 'usr-admin-1',
        name: 'Anita (Admin)',
        role: 'Admin',
      },
    }
    setAuthSession(mockRes.token, mockRes.user)
    return mockRes
  }

  const res = await post<LoginResponse>('/auth/pin', { pin })
  setAuthSession(res.token, res.user)
  return res
}

export async function fetchMe(): Promise<MeResponse> {
  if (USE_MOCKS) {
    return {
      id: 'usr-admin-1',
      name: 'Anita (Admin)',
      role: 'Admin',
      camera_ids: ['cam-east-wing', 'cam-south-gate', 'cam-warehouse-a', 'cam-roof-level'],
      actions: [
        'all_cameras',
        'ack',
        'resolve',
        'manage_users',
        'view_audit',
        'manage_zones',
        'manage_responders',
        'alert_all',
        'broadcast',
        'assistant',
        'run_demo',
      ],
    }
  }
  return get<MeResponse>('/me')
}

/* ── Health ─────────────────────────────────────────────── */

export async function fetchHealth(): Promise<HealthResponse> {
  if (USE_MOCKS) {
    const cameras = await mockCameras()
    return {
      cameras: cameras.map(c => ({ id: c.id, online: c.online, fps: c.fps })),
      db: 'ok',
      twilio: 'ok',
      llm: 'ok',
    }
  }
  return get<HealthResponse>('/health')
}

/* ── Cameras ────────────────────────────────────────────── */

export async function fetchCameras(): Promise<Camera[]> {
  if (USE_MOCKS) return mockCameras()
  const list = await get<Camera[]>('/cameras')
  return list.map(normalizeCamera)
}

/**
 * Fetches a fresh signed stream URL for a camera.
 * Called when stream token expires or <img> fails to load.
 */
export async function fetchCameraStreamUrl(id: string): Promise<string> {
  if (USE_MOCKS) {
    return `${API_URL}/stream/${id}`
  }
  const res = await get<CameraStreamUrlResponse>(`/cameras/${id}/stream-url`)
  return resolveMediaUrl(res.stream_url)
}

export function getStreamUrl(cameraId: string): string {
  return `${API_URL}/stream/${cameraId}`
}

/* ── Events (Incidents) ─────────────────────────────────── */

export async function fetchEvents(filters?: EventFilters): Promise<Incident[]> {
  if (USE_MOCKS) {
    let list = await mockIncidents()
    if (filters?.type)     list = list.filter(i => i.type === filters.type)
    if (filters?.camera)   list = list.filter(i => i.camera_id === filters.camera)
    if (filters?.severity) list = list.filter(i => i.severity === filters.severity)
    if (filters?.status)   list = list.filter(i => i.status === filters.status)
    if (filters?.search) {
      const q = filters.search.toLowerCase()
      list = list.filter(i =>
        i.camera_name.toLowerCase().includes(q) ||
        i.type.toLowerCase().includes(q) ||
        i.zone.toLowerCase().includes(q),
      )
    }
    return list.sort(
      (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
    )
  }

  const params = new URLSearchParams()
  if (filters?.type)     params.set('type', filters.type)
  if (filters?.camera)   params.set('camera', filters.camera)
  if (filters?.severity) params.set('severity', filters.severity)
  if (filters?.status)   params.set('status', filters.status)
  if (filters?.from)     params.set('from', filters.from)
  if (filters?.to)       params.set('to', filters.to)
  if (filters?.limit)    params.set('limit', String(filters.limit))
  const qs = params.toString()
  const incidents = await get<Incident[]>(`/events${qs ? `?${qs}` : ''}`)
  return incidents.map(normalizeIncident)
}

export async function fetchEvent(id: number): Promise<Incident> {
  if (USE_MOCKS) {
    const list = await mockIncidents()
    const inc = list.find(i => i.id === id)
    if (!inc) throw new Error(`Mock: incident ${id} not found`)
    return normalizeIncident(inc)
  }
  const incident = await get<Incident>(`/events/${id}`)
  return normalizeIncident(incident)
}

/**
 * Acknowledges an incident. Note: user comes directly from the Bearer token.
 */
export async function acknowledgeEvent(id: number): Promise<Incident> {
  if (USE_MOCKS) {
    const inc = await fetchEvent(id)
    return { ...inc, status: 'acknowledged' }
  }
  const incident = await post<Incident>(`/events/${id}/ack`)
  return normalizeIncident(incident)
}

/**
 * Resolves an incident with outcome ("resolved" | "false_alarm") and optional note.
 */
export async function resolveEvent(
  id: number,
  outcome: 'resolved' | 'false_alarm',
  note?: string,
): Promise<Incident> {
  if (USE_MOCKS) {
    const inc = await fetchEvent(id)
    return { ...inc, status: outcome }
  }
  const incident = await post<Incident>(`/events/${id}/resolve`, { outcome, note })
  return normalizeIncident(incident)
}

/* ── Zones ──────────────────────────────────────────────── */

export async function fetchZones(): Promise<ZonePayload[]> {
  if (USE_MOCKS) {
    const cameras = await mockCameras()
    return cameras.map(c => ({ camera_id: c.id, zones: c.zones }))
  }
  return get<ZonePayload[]>('/zones')
}

export async function saveZones(payload: ZonePayload): Promise<ZonePayload> {
  if (USE_MOCKS) return Promise.resolve(payload)
  return put<ZonePayload>('/zones', payload)
}

/* ── Responders ─────────────────────────────────────────── */

export async function fetchResponders(): Promise<Responder[]> {
  if (USE_MOCKS) return mockResponders()
  return get<Responder[]>('/responders')
}

export async function createResponder(r: Omit<Responder, 'id'>): Promise<Responder> {
  if (USE_MOCKS) return { ...r, id: `resp-${Date.now()}` }
  return post<Responder>('/responders', r)
}

export async function updateResponder(r: Responder): Promise<Responder> {
  if (USE_MOCKS) return r
  return put<Responder>(`/responders/${r.id}`, r)
}

export async function deleteResponder(id: string): Promise<void> {
  if (USE_MOCKS) return
  await del<void>(`/responders/${id}`)
}

/* ── Stats ──────────────────────────────────────────────── */

export async function fetchStats(from?: string, to?: string): Promise<StatsResponse> {
  if (USE_MOCKS) return mockStats()
  const params = new URLSearchParams()
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  const qs = params.toString()
  const raw = await get<StatsResponse>(`/stats${qs ? `?${qs}` : ''}`)
  return {
    ...raw,
    open: raw.open ?? raw.open_count ?? 0,
    resolved: raw.resolved ?? raw.resolved_count ?? 0,
    avg_response_s: raw.avg_response_s ?? raw.avg_response_seconds ?? null,
    open_count: raw.open_count ?? raw.open ?? 0,
    resolved_count: raw.resolved_count ?? raw.resolved ?? 0,
    avg_response_seconds: raw.avg_response_seconds ?? raw.avg_response_s ?? null,
  }
}

/* ── Alerts & Roll Call ─────────────────────────────────── */

export async function alertAll(message: string, incident_id?: number): Promise<AlertAllNotice> {
  if (USE_MOCKS) {
    return {
      kind: 'alert_all',
      message,
      incident_id,
      at: new Date().toISOString(),
      by: 'Admin',
      recipients: ['Ravi Kumar', 'Anand Pillai'],
      whatsapp_sent: 2,
      calls_queued: 2,
    }
  }
  return post<AlertAllNotice>('/alerts/all', { message, incident_id })
}

export async function fetchRollCall(incident_id?: number): Promise<RollCallResponse> {
  if (USE_MOCKS) {
    return {
      total: 4,
      responding: 2,
      safe: 1,
      not_reached: 1,
      guards: [
        { id: 'resp-001', name: 'Ravi Kumar', status: 'safe' },
        { id: 'resp-002', name: 'Anand Pillai', status: 'responding' },
        { id: 'resp-003', name: 'Suresh Nair', status: 'responding' },
        { id: 'resp-004', name: 'Deepak Rao', status: 'not_reached' },
      ],
    }
  }
  const qs = incident_id ? `?incident_id=${incident_id}` : ''
  return get<RollCallResponse>(`/roll-call${qs}`)
}

/* ── Assistant ──────────────────────────────────────────── */

export async function queryAssistant(question: string): Promise<AssistantQueryResponse> {
  if (USE_MOCKS) {
    const incidents = (await mockIncidents()).slice(0, 3)
    return {
      answer: `Based on today's monitoring data, I found ${incidents.length} relevant incidents matching your query: "${question}". The most recent critical event was a fire alert at East Wing, Loading Bay 2.`,
      incidents,
      source: 'groq',
      ms: 120,
    }
  }
  const res = await post<AssistantQueryResponse>('/assistant/query', { question })
  return {
    ...res,
    incidents: (res.incidents || []).map(normalizeIncident),
  }
}

export async function queryAssistantVoice(audio: Blob): Promise<AssistantVoiceResponse> {
  if (USE_MOCKS) {
    const incidents = (await mockIncidents()).slice(0, 2)
    return {
      transcript: '(mock voice) Show me fire incidents from today',
      answer: `I found ${incidents.length} fire-related incidents in the past 24 hours.`,
      incidents,
      source: 'groq',
      ms: 310,
    }
  }
  const form = new FormData()
  form.append('audio', audio, 'query.webm')
  const res = await postForm<AssistantVoiceResponse>('/assistant/voice', form)
  return {
    ...res,
    incidents: (res.incidents || []).map(normalizeIncident),
  }
}

export async function assistantCommand(text: string): Promise<AssistantCommandResponse> {
  if (USE_MOCKS) {
    return {
      intent: 'status_check',
      status: 'done',
      answer: 'All systems normal.',
      ms: 95,
    }
  }
  return post<AssistantCommandResponse>('/assistant/command', { text })
}

export async function assistantConfirm(
  confirm_token: string,
  confirmed: boolean,
): Promise<AssistantConfirmResponse> {
  if (USE_MOCKS) {
    return {
      status: 'done',
      answer: 'Command confirmed and executed.',
      ms: 80,
    }
  }
  return post<AssistantConfirmResponse>('/assistant/confirm', { confirm_token, confirmed })
}

/* ── Broadcast ──────────────────────────────────────────── */

export async function broadcastMessage(audio: Blob, zone: string): Promise<void> {
  if (USE_MOCKS) return
  const form = new FormData()
  form.append('audio', audio, 'broadcast.webm')
  form.append('zone', zone)
  await postForm<void>('/broadcast', form)
}

/* ── Audit Log & Sector Summary ─────────────────────────── */

export async function fetchAuditLog(params?: {
  user?: string
  action?: string
  channel?: string
  from?: string
  to?: string
  limit?: number
}): Promise<AuditLogRow[]> {
  if (USE_MOCKS) {
    return [
      {
        at: new Date().toISOString(),
        user: 'Anita (Admin)',
        action: 'acknowledged',
        target: 'incident:128',
        channel: 'dashboard',
      },
    ]
  }
  const q = new URLSearchParams()
  if (params?.user)    q.set('user', params.user)
  if (params?.action)  q.set('action', params.action)
  if (params?.channel) q.set('channel', params.channel)
  if (params?.from)    q.set('from', params.from)
  if (params?.to)      q.set('to', params.to)
  if (params?.limit)   q.set('limit', String(params.limit))
  const qs = q.toString()
  return get<AuditLogRow[]>(`/audit-log${qs ? `?${qs}` : ''}`)
}

export async function fetchSummary(from?: string, to?: string): Promise<SectorSummary> {
  const q = new URLSearchParams()
  if (from) q.set('from', from)
  if (to)   q.set('to', to)
  const qs = q.toString()
  return get<SectorSummary>(`/summary${qs ? `?${qs}` : ''}`)
}

/* ── Demo alarm ─────────────────────────────────────────── */

export type DemoIncidentType = 'fire' | 'smoke' | 'no_helmet' | 'fall' | 'access'

/**
 * Raises a demo alarm (needs the run_demo action). It is a real incident: every dashboard gets it and the
 * real call ladder starts. In mock mode a fake incident with the first ladder steps comes back.
 */
export async function raiseDemoIncident(type: DemoIncidentType, camera_id?: string): Promise<Incident> {
  if (USE_MOCKS) {
    const now = new Date().toISOString()
    return {
      id: Math.floor(Math.random() * 1000) + 5000,
      type,
      severity: type === 'access' ? 'high' : 'critical',
      status: 'open',
      camera_id: camera_id ?? 'cam-east-wing',
      camera_name: 'East Wing, Loading Bay 2',
      zone: 'loading-bay',
      started_at: now,
      confidence: 1,
      snapshot_url: '',
      clip_url: '',
      timeline: [
        { at: now, action: 'detected' },
        { at: now, action: 'call_placed', to: 'Ravi Kumar' },
        { at: now, action: 'whatsapp_sent', to: 'Anita Rao' },
      ],
    }
  }
  const incident = await post<Incident>('/demo/incident', { type, camera_id })
  return normalizeIncident(incident)
}

/* ── Raw helpers for the admin screens (lib/admin.ts) ───── */

export const http = { get, post, put, del, form: postForm }
