/**
 * lib/timeline.tsx
 * Plain words for each timeline step of an incident (used by the demo panel and the incident page).
 */
import { ArrowUpRight, Check, Info, MessageCircle, Phone, Siren } from 'lucide-react'
import type { TimelineEvent } from './types'

export function describe(e: TimelineEvent): { text: string; icon: typeof Phone; tone: string } {
  const who = e.to ?? e.by ?? ''
  switch (e.action) {
    case 'detected': return { text: 'Detected', icon: Siren, tone: 'var(--critical)' }
    case 'call_placed': return { text: `Calling ${who}`, icon: Phone, tone: '#ffffff' }
    case 'call_no_answer': return { text: `No answer from ${who}`, icon: Phone, tone: 'var(--high)' }
    case 'whatsapp_sent': return { text: `WhatsApp to ${who}`, icon: MessageCircle, tone: '#ffffff' }
    case 'escalated': return { text: `Escalating to ${who}`, icon: ArrowUpRight, tone: 'var(--high)' }
    case 'call_skipped_on_screen': return { text: `${who} reacted on screen: call cancelled`, icon: Check, tone: 'var(--resolved)' }
    case 'duty_fallback': return { text: `Nobody on duty: using the shift roster (${who})`, icon: Info, tone: 'var(--high)' }
    case 'acknowledged': return { text: `Acknowledged by ${who}${e.via ? ` (${e.via})` : ''}`, icon: Check, tone: 'var(--resolved)' }
    case 'resolved': return { text: `Resolved by ${who}`, icon: Check, tone: 'var(--resolved)' }
    case 'false_alarm': return { text: `False alarm (${who})`, icon: Check, tone: 'var(--neutral)' }
    default: return { text: `${e.action.replace(/_/g, ' ')}${who ? `: ${who}` : ''}`, icon: Info, tone: 'var(--text-secondary)' }
  }
}

