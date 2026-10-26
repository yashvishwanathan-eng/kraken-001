/**
 * components/StatusChip.tsx
 * Incident status indicator with icon + word. Clean professional appearance.
 * Status colours: open (critical red), acknowledged (high amber), resolved (resolved green), false alarm (neutral).
 */

import { Clock, CheckCircle, XCircle, CheckCheck } from 'lucide-react'
import type { IncidentStatus } from '@/lib/types'

interface Props {
  status: IncidentStatus
  size?: 'sm' | 'md'
}

const CONFIG: Record<IncidentStatus, { label: string; icon: React.ReactNode; classes: string }> = {
  open: {
    label: 'Open',
    icon: <Clock className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#f43f5e]/10 text-[#f43f5e] border-[#f43f5e]/25',
  },
  acknowledged: {
    label: 'Acknowledged',
    icon: <CheckCircle className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/25',
  },
  resolved: {
    label: 'Resolved',
    icon: <CheckCheck className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#10b981]/10 text-[#10b981] border-[#10b981]/25',
  },
  false_alarm: {
    label: 'False Alarm',
    icon: <XCircle className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-white/5 text-[var(--text-secondary)] border-white/10',
  },
}

export function StatusChip({ status, size = 'md' }: Props) {
  const conf = CONFIG[status] || CONFIG.open
  const sizeClass =
    size === 'sm'
      ? 'text-[10px] px-2 py-0.5 gap-1.5 font-mono'
      : 'text-xs px-2.5 py-1 gap-1.5 font-mono'

  return (
    <span
      className={`inline-flex items-center font-medium border rounded-[6px] select-none ${sizeClass} ${conf.classes}`}
      role="status"
      aria-label={`Status: ${conf.label}`}
    >
      {conf.icon}
      <span>{conf.label}</span>
    </span>
  )
}
