/**
 * components/demo/DemoAlert.tsx
 * "Demo alert" button in the top bar (only for users with the run_demo action).
 * Raises a demo fire, fall or restricted entry, then shows a live panel of what the system does:
 * detection, calls, WhatsApp, escalation, on-screen grace and acknowledgement, with seconds since detection.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bike, CloudFog, DoorOpen, Flame, PersonStanding, Siren, X } from 'lucide-react'
import { describe } from '@/lib/timeline'
import { incidentColor } from '@/lib/incidentLook'
import { acknowledgeEvent, fetchCameras, fetchHealth, fetchMe, raiseDemoIncident, type DemoIncidentType } from '@/lib/api'
import { wsClient } from '@/lib/ws'
import type { Incident, WsEvent } from '@/lib/types'

const TYPES: { type: DemoIncidentType; label: string; icon: typeof Flame }[] = [
  { type: 'fire', label: 'Fire', icon: Flame },
  { type: 'smoke', label: 'Smoke', icon: CloudFog },
  { type: 'no_helmet', label: 'No helmet', icon: Bike },
  { type: 'fall', label: 'Person down', icon: PersonStanding },
  { type: 'access', label: 'Restricted entry', icon: DoorOpen },
]

/** The incident inside a WebSocket message, if the message is about one. */
function incidentFrom(event: WsEvent): Incident | null {
  if (!event || typeof event !== 'object') return null
  if ('timeline' in event && 'id' in event) return event as Incident
  if ('data' in event && event.data && typeof event.data === 'object' && 'timeline' in event.data) {
    return event.data as Incident
  }
  return null
}

/** Timelines only grow: when updates arrive out of order, keep the one that knows more. */
function newer(prev: Incident | null, next: Incident): Incident {
  if (!prev || prev.id !== next.id) return next
  return next.timeline.length >= prev.timeline.length ? next : prev
}

export function DemoAlert() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const { data: cameras } = useQuery({ queryKey: ['cameras'], queryFn: fetchCameras, enabled: Boolean(me) })
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const [menuOpen, setMenuOpen] = useState(false)
  const [cameraId, setCameraId] = useState<string>('')
  const [incident, setIncident] = useState<Incident | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Live updates for the demo incident: every new timeline step arrives on the WebSocket.
  useEffect(() => {
    if (!incident) return
    return wsClient.subscribe(event => {
      const update = incidentFrom(event)
      if (update && update.id === incident.id) setIncident(prev => newer(prev, update))
    })
  }, [incident?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const started = useMemo(() => (incident ? Date.parse(incident.started_at) : 0), [incident])

  if (!me?.actions?.includes('run_demo')) return null

  const raise = async (type: DemoIncidentType) => {
    setBusy(true)
    setError(null)
    try {
      setIncident(await raiseDemoIncident(type, cameraId || undefined))
      setMenuOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not raise the demo alarm')
    } finally {
      setBusy(false)
    }
  }

  const acknowledge = async () => {
    if (!incident) return
    try {
      const acked = await acknowledgeEvent(incident.id)
      setIncident(prev => newer(prev, acked))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not acknowledge')
    }
  }

  return (
    <>
      <div className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen(o => !o)}
          aria-expanded={menuOpen}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-[10px] text-xs font-mono border cursor-pointer transition-colors"
          style={{ color: 'var(--critical)', borderColor: 'rgba(244, 63, 94, 0.4)', background: 'var(--critical-subtle)' }}
        >
          <Siren className="w-3.5 h-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">Demo alert</span>
        </button>

        {menuOpen && (
          <div
            className="absolute right-0 top-9 z-50 w-64 rounded-[14px] border p-3 shadow-2xl"
            style={{ background: 'var(--surface-raised)', borderColor: 'var(--border)' }}
            role="dialog"
            aria-label="Raise a demo alarm"
          >
            <p className="text-[11px] text-[var(--text-muted)] mb-2">
              A real test alarm: every dashboard sees it and the call ladder starts.
            </p>
            <label className="block text-[11px] text-[var(--text-secondary)] mb-1" htmlFor="demo-camera">Camera</label>
            <select
              id="demo-camera"
              value={cameraId}
              onChange={e => setCameraId(e.target.value)}
              className="w-full mb-3 rounded-[8px] border bg-black px-2 py-1.5 text-xs text-white"
              style={{ borderColor: 'var(--border)' }}
            >
              <option value="">First camera</option>
              {(cameras ?? []).map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            <div className="grid gap-1.5">
              {TYPES.map(({ type, label, icon: Icon }) => (
                <button
                  key={type}
                  type="button"
                  disabled={busy}
                  onClick={() => raise(type)}
                  className="flex items-center gap-2 rounded-[10px] border px-3 py-2 text-left text-xs text-white hover:bg-white/5 cursor-pointer disabled:opacity-50"
                  style={{ borderColor: 'var(--border)' }}
                >
                  <Icon className="w-3.5 h-3.5" aria-hidden="true" style={{ color: incidentColor(type, type === 'no_helmet' ? 'low' : 'critical') }} />
                  {label}
                </button>
              ))}
            </div>
            {error && <p className="mt-2 text-[11px]" style={{ color: 'var(--critical)' }}>{error}</p>}
          </div>
        )}
      </div>

      {incident && (
        <section
          className="fixed bottom-4 right-4 z-50 w-[340px] max-w-[calc(100vw-2rem)] rounded-[20px] border p-4 shadow-2xl"
          style={{ background: 'var(--surface)', borderColor: 'rgba(244, 63, 94, 0.35)' }}
          aria-live="polite"
          aria-label="Demo alarm progress"
        >
          <header className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[11px] font-mono" style={{ color: 'var(--critical)' }}>
                DEMO · #{incident.id} · {incident.severity.toUpperCase()}
              </p>
              <p className="mt-0.5 truncate text-sm font-semibold text-white">
                {TYPES.find(t => t.type === incident.type)?.label ?? incident.type} · {incident.camera_name}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setIncident(null)}
              className="rounded-full p-1 text-[var(--text-secondary)] hover:text-white hover:bg-white/5 cursor-pointer"
              aria-label="Close demo panel"
            >
              <X className="w-4 h-4" />
            </button>
          </header>

          {health && health.twilio !== 'ok' && (
            <p className="mt-2 rounded-[10px] px-2.5 py-1.5 text-[11px]" style={{ background: 'var(--high-subtle)', color: 'var(--high)' }}>
              Twilio is not set up: calls are simulated (CALLS_MODE=simulate) or off. Phones will not ring.
            </p>
          )}

          <ol className="mt-3 space-y-1.5">
            {incident.timeline.map((e, i) => {
              const { text, icon: Icon, tone } = describe(e)
              const secs = Math.max(0, (Date.parse(e.at) - started) / 1000)
              return (
                <li key={`${e.at}-${i}`} className="flex items-start gap-2.5 text-xs">
                  <span className="w-12 shrink-0 text-right font-mono tabular text-[var(--text-muted)]">+{secs.toFixed(1)}s</span>
                  <Icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" style={{ color: tone }} />
                  <span className="text-white leading-snug">{text}</span>
                </li>
              )
            })}
          </ol>

          {incident.status === 'open' ? (
            <button
              type="button"
              onClick={acknowledge}
              className="mt-3 w-full rounded-[12px] py-2 text-xs font-semibold text-black bg-white hover:bg-white/90 cursor-pointer"
            >
              Acknowledge (within 5 s cancels the Admin's call)
            </button>
          ) : (
            <p className="mt-3 text-center text-xs" style={{ color: 'var(--resolved)' }}>
              {incident.status.replace('_', ' ')}{incident.acknowledged_by ? ` by ${incident.acknowledged_by}` : ''}
            </p>
          )}
        </section>
      )}
    </>
  )
}
