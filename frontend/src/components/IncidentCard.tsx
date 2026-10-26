/**
 * components/IncidentCard.tsx
 * Pro dark bento incident card for the live feed.
 * Solid near-black (#0b0b0c), 1px border at rgba(255,255,255,0.08), radius 20px.
 * Monospace font for times, IDs, and camera names.
 */

import { useNavigate } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { SeverityBadge } from '@/components/SeverityBadge'
import { formatIncidentTime } from '@/lib/time'
import type { Incident } from '@/lib/types'

interface IncidentCardProps {
  incident: Incident
}

export function IncidentCard({ incident }: IncidentCardProps) {
  const navigate = useNavigate()

  return (
    <div
      onClick={() => navigate(`/incidents/${incident.id}`)}
      className="group relative p-4 rounded-[20px] border transition-colors duration-200 cursor-pointer select-none"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.08)'
      }}
      role="button"
      tabIndex={0}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          navigate(`/incidents/${incident.id}`)
        }
      }}
      aria-label={`Incident #${incident.id}: ${incident.type} at ${incident.camera_name}`}
    >
      {/* Top row: Severity badge & Incident Type */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <SeverityBadge severity={incident.severity} size="sm" />
        <span className="text-[11px] font-mono font-medium uppercase tracking-wider px-2 py-0.5 rounded-[6px] bg-white/5 text-[var(--text-secondary)] border border-white/10">
          {incident.type}
        </span>
      </div>

      {/* Middle row: Camera name and zone */}
      <div className="flex items-center justify-between gap-2 text-xs text-white mb-2.5">
        <span className="font-medium truncate">{incident.camera_name}</span>
        <span className="font-mono text-[11px] text-[var(--text-muted)] shrink-0">
          #{incident.id}
        </span>
      </div>

      {/* Bottom row: IST Timestamp + Arrow indicator */}
      <div className="flex items-center justify-between text-[11px] text-[var(--text-muted)] font-mono pt-2 border-t border-white/[0.06]">
        <span>{formatIncidentTime(incident.started_at)}</span>
        <ChevronRight className="w-3.5 h-3.5 text-[var(--text-muted)] group-hover:text-white transition-colors" />
      </div>
    </div>
  )
}
