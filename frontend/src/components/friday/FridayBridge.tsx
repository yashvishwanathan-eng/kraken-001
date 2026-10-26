/**
 * components/friday/FridayBridge.tsx
 * Connects Friday to the rest of the app (mounted once in the layout):
 *  - an alarm (a new critical or high incident) puts a red glow around the whole app until it is acknowledged,
 *    and Friday says what happened, once per alarm. She advises; she never acts by herself.
 *  - "friday" notices from the wake-word listener: "Hey Friday" opens her page and shows listening / thinking,
 *    and her answer is spoken here when this dashboard belongs to the listener's user (FRIDAY_USER).
 */
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Siren } from 'lucide-react'
import { acknowledgeEvent } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { isSpeaker, setSpeaker, showState, speak } from '@/lib/friday'
import { incidentRgb } from '@/lib/incidentLook'
import { wsClient } from '@/lib/ws'
import type { Incident } from '@/lib/types'

const LABEL: Record<string, string> = {
  fire: 'Fire detected', smoke: 'Smoke detected', no_helmet: 'Rider without a helmet', fall: 'Person down', access: 'Restricted area entry', unattended: 'Unattended bag',
  crowding: 'Crowding', ppe: 'Missing helmet or vest', loitering: 'Loitering',
}

interface FridayNotice {
  kind: 'friday'
  state: 'idle' | 'listening' | 'thinking' | 'speaking'
  heard?: string
  answer?: string
  speak_for_user_id?: number | null
  camera_ids?: string[] | null
  open_path?: string | null
}

function incidentFrom(event: unknown): Incident | null {
  if (!event || typeof event !== 'object') return null
  if ('timeline' in event && 'severity' in event) return event as Incident
  const data = (event as { data?: unknown }).data
  return data && typeof data === 'object' && 'timeline' in data ? (data as Incident) : null
}

export function alarmSentence(i: Incident): string {
  const camera = i.camera_name || i.camera_id
  const firstCall = i.timeline.find(t => t.action === 'call_placed')?.to
  return `${LABEL[i.type] ?? i.type} on ${camera}! ${firstCall ? `I'm calling ${firstCall} now. ` : "I'm calling the head guard now. "}` +
    (i.severity === 'critical' ? 'Say evacuate to alert everyone, or acknowledge it.' : 'Acknowledge it when someone is on the way.')
}

export function FridayBridge() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const { user } = useAuth()
  const [alarm, setAlarm] = useState<Incident | null>(null)
  const announced = useRef<Set<number>>(new Set())
  const pathRef = useRef(pathname)
  pathRef.current = pathname

  useEffect(() => wsClient.subscribe(event => {
    const notice = event as unknown as FridayNotice
    const kind = (event as { kind?: string } | null)?.kind
    if (kind === 'speaker') {
      setSpeaker(Boolean((event as unknown as { active?: boolean }).active))
      return
    }
    if (notice && typeof notice === 'object' && notice.kind === 'friday') {
      // "show cameras 3 and 4": open them now, while she is still talking
      if (notice.camera_ids?.length) navigate(`/live?cameras=${encodeURIComponent(notice.camera_ids.join(','))}`)
      else if (notice.open_path?.startsWith('/')) navigate(notice.open_path)
      if (notice.state === 'listening' && pathRef.current !== '/assistant') navigate('/assistant')
      showState(notice.state === 'speaking' ? 'speaking' : notice.state, { heard: notice.heard, answer: notice.answer })
      if (notice.state === 'speaking' && notice.answer && isSpeaker() && notice.speak_for_user_id && String(notice.speak_for_user_id) === String(user?.id)) {
        void speak(notice.answer)
      }
      return
    }
    const inc = incidentFrom(event)
    if (!inc) return
    setAlarm(current => (current && current.id === inc.id ? (inc.status === 'open' ? inc : null) : current))
    if (inc.status !== 'open' || (inc.severity !== 'critical' && inc.severity !== 'high') || announced.current.has(inc.id)) return
    announced.current.add(inc.id)
    setAlarm(inc)
    showState('alarm', { alarmRgb: incidentRgb(inc.type, inc.severity) })
    if (isSpeaker()) void speak(alarmSentence(inc), 'urgent') // an emergency: her urgent voice
  }), [navigate, user?.id])

  const acknowledge = async () => {
    if (!alarm) return
    try {
      await acknowledgeEvent(alarm.id)
    } finally {
      setAlarm(null)
      showState('idle')
    }
  }

  // fire: orange glow, smoke: grey, every other emergency: red (its severity colour)
  const tone = alarm ? incidentRgb(alarm.type, alarm.severity) : '239,68,68'

  return (
    <AnimatePresence>
      {alarm && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="pointer-events-none fixed inset-0 z-[55]">
          <motion.div
            className="absolute inset-0"
            animate={{ boxShadow: [`inset 0 0 60px 6px rgba(${tone},0.55)`, `inset 0 0 120px 18px rgba(${tone},0.85)`, `inset 0 0 60px 6px rgba(${tone},0.55)`] }}
            transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
          />
          <div className="pointer-events-auto absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-3 rounded-full border px-4 py-2 shadow-2xl"
               style={{ background: 'rgba(14,10,8,0.94)', borderColor: `rgba(${tone},0.7)` }} role="alert">
            <Siren className="h-4 w-4 animate-pulse" style={{ color: `rgb(${tone})` }} aria-hidden="true" />
            <span className="text-sm font-medium text-white">{LABEL[alarm.type] ?? alarm.type} · {alarm.camera_name}</span>
            <button type="button" onClick={() => navigate(`/incidents/${alarm.id}`)} className="rounded-full border border-white/20 px-3 py-1 text-xs text-white hover:bg-white/10 cursor-pointer">Open</button>
            <button type="button" onClick={acknowledge} className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-black hover:bg-white/90 cursor-pointer">Acknowledge</button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
