/**
 * components/SeverityBadge.tsx
 * Severity indicator with icon + word. Clean professional appearance.
 * Severity colours stay: critical red, high amber, low blue.
 */

import { AlertTriangle, AlertCircle, Info } from 'lucide-react'
import { incidentRgb } from '@/lib/incidentLook'
import type { Severity } from '@/lib/types'

interface Props {
  severity: Severity
  size?: 'sm' | 'md'
  type?: string // fire is orange and smoke grey, whatever the severity
}

const CONFIG: Record<Severity, { label: string; icon: React.ReactNode; classes: string }> = {
  critical: {
    label: 'Critical',
    icon: <AlertCircle className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#f43f5e]/10 text-[#f43f5e] border-[#f43f5e]/25',
  },
  high: {
    label: 'High',
    icon: <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#f59e0b]/10 text-[#f59e0b] border-[#f59e0b]/25',
  },
  low: {
    label: 'Low',
    icon: <Info className="w-3 h-3 shrink-0" aria-hidden="true" />,
    classes: 'bg-[#3b82f6]/10 text-[#3b82f6] border-[#3b82f6]/25',
  },
}

export function SeverityBadge({ severity, size = 'md', type }: Props) {
  const conf = CONFIG[severity] || CONFIG.low
  const tinted = type === 'fire' || type === 'smoke' // fire orange, smoke grey
  const rgb = tinted ? incidentRgb(type, severity) : null
  const sizeClass =
    size === 'sm'
      ? 'text-[10px] px-2 py-0.5 gap-1.5 font-mono'
      : 'text-xs px-2.5 py-1 gap-1.5 font-mono'

  return (
    <span
      className={`inline-flex items-center font-medium border rounded-[6px] select-none ${sizeClass} ${rgb ? '' : conf.classes}`}
      style={rgb ? { color: `rgb(${rgb})`, background: `rgba(${rgb},0.12)`, borderColor: `rgba(${rgb},0.35)` } : undefined}
      role="status"
      aria-label={`Severity: ${conf.label}`}
    >
      {conf.icon}
      <span>{conf.label}</span>
    </span>
  )
}
