/**
 * pages/Dashboard.tsx
 * The main admin dashboard, bento style (layout ideas from the Web3 bento component Kirito supplied, filled
 * with RingGuard data): cameras running, problems now, past 7 days, guards on and off duty, today's summary,
 * cameras list, incidents over 30 days and data usage. Everything comes from /overview, /summary and /usage.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Cpu, ShieldCheck } from 'lucide-react'
import { bytes, fetchOverview, fetchSummary, fetchUsage, type Overview, type Usage } from '@/lib/admin'
import { Dot, ErrorLine, istHour, SEVERITY_COLOR, Tile, useCountUp, useMe } from '@/components/admin/kit'
import { useAuth } from '@/lib/auth'

const ACCENT = 'oklch(0.89 0.2 128)' // lime, as in the bento component
const ACCENT_INK = 'oklch(0.24 0.06 135)'

function greeting() {
  const h = istHour()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

function dayLabel(date: string) {
  return new Date(`${date}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short' })
}

function HeroTile({ o, name }: { o: Overview; name: string }) {
  const online = useCountUp(o.cameras_online)
  const open = useCountUp(o.open_incidents)
  const max = Math.max(1, ...o.past_problems.map(d => d.count))
  const [hover, setHover] = useState<number | null>(null)
  return (
    <Tile className="sm:col-span-2 lg:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <p className="text-[28px] font-semibold leading-[1.1] tracking-[-0.03em] sm:text-[32px]">
            {greeting()}, <span style={{ color: ACCENT }}>{name.split(' ')[0]}</span>
          </p>
          <p className="mt-1 max-w-md text-sm text-muted-foreground">{o.text}</p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <p className="text-[12px] text-muted-foreground">Cameras online</p>
            <p className="font-mono text-[36px] font-semibold leading-none tabular">
              {Math.round(online)}<span className="text-lg text-muted-foreground">/{o.cameras_total}</span>
            </p>
          </div>
          <div>
            <p className="text-[12px] text-muted-foreground">Open incidents</p>
            <p className="font-mono text-[36px] font-semibold leading-none tabular" style={{ color: o.open_incidents ? 'var(--critical)' : undefined }}>
              {Math.round(open)}
            </p>
          </div>
        </div>
      </div>
      <div className="mt-6 flex flex-1 flex-col">
        <div className="flex flex-1 items-end gap-2" style={{ minHeight: 72 }} onPointerLeave={() => setHover(null)}>
          {o.past_problems.map((d, i) => (
            <button
              key={d.date}
              type="button"
              onPointerEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              aria-label={`${d.date}: ${d.count} incidents`}
              className="flex-1 rounded-[6px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              style={{
                height: `${12 + (d.count / max) * 88}%`,
                background: d.count ? ACCENT : 'var(--surface-hover)',
                opacity: hover === null || hover === i ? 1 : 0.35,
                transition: 'height 900ms cubic-bezier(0.22,1,0.36,1), opacity 200ms',
              }}
            />
          ))}
        </div>
        <div className="mt-2 flex justify-between font-mono text-[11px] text-muted-foreground">
          <span>{dayLabel(o.past_problems[0]?.date ?? '')}</span>
          <span className="text-white" aria-live="polite">
            {hover !== null ? `${o.past_problems[hover].date} · ${o.past_problems[hover].count} incidents` : `${o.past_total_7d} incidents in 7 days`}
          </span>
          <span>Today</span>
        </div>
      </div>
    </Tile>
  )
}

function ProblemsTile({ o }: { o: Overview }) {
  const navigate = useNavigate()
  return (
    <Tile
      title="Problems now"
      className="lg:row-span-2"
      action={<span className="font-mono text-[11px] text-muted-foreground">{o.problems_now.length}</span>}
    >
      {o.ok ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-6 text-center">
          <CheckCircle2 className="h-8 w-8" style={{ color: 'var(--resolved)' }} />
          <p className="text-sm text-white">All clear</p>
          <p className="text-xs text-muted-foreground">No open incidents, every camera online.</p>
        </div>
      ) : (
        <ul className="-mx-2 space-y-1 overflow-y-auto" style={{ maxHeight: 360 }}>
          {o.problems_now.map((p, i) => (
            <li key={i}>
              <button
                type="button"
                disabled={!p.incident_id}
                onClick={() => p.incident_id && navigate(`/incidents/${p.incident_id}`)}
                className="flex w-full items-start gap-2.5 rounded-2xl px-2 py-2 text-left hover:bg-white/5 disabled:cursor-default cursor-pointer"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: SEVERITY_COLOR[p.severity] }} />
                <span className="text-[13px] leading-snug text-white">{p.text}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Tile>
  )
}

function GuardsTile({ o }: { o: Overview }) {
  const on = useCountUp(o.guards_on_duty.length)
  return (
    <section className="relative flex min-w-0 flex-col overflow-hidden rounded-[26px] p-5 lg:row-span-2" style={{ background: ACCENT, color: ACCENT_INK }}>
      <header className="flex items-center justify-between">
        <h3 className="text-[13px] font-semibold opacity-75">Guards</h3>
        <span className="flex items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[11px] font-semibold" style={{ background: 'oklch(0.24 0.06 135 / 0.12)' }}>
          <ShieldCheck className="h-3 w-3" /> on duty
        </span>
      </header>
      <p className="mt-5 font-mono text-[56px] font-semibold leading-none tracking-[-0.05em] tabular">{Math.round(on)}</p>
      <p className="mt-1 text-[12px] font-medium opacity-70">
        of {o.guards_on_duty.length + o.guards_off_duty.length} guards checked in
      </p>
      <ul className="mt-5 space-y-1.5 text-[13px] font-medium">
        {o.guards_on_duty.map(n => (
          <li key={n} className="flex items-center gap-2"><span className="size-1.5 rounded-full" style={{ background: ACCENT_INK }} />{n}</li>
        ))}
      </ul>
      {o.guards_off_duty.length > 0 && (
        <div className="mt-auto pt-6">
          <p className="text-[12px] font-semibold opacity-70">Off duty (not called)</p>
          <p className="mt-1 text-[13px] opacity-80">{o.guards_off_duty.join(', ')}</p>
        </div>
      )}
    </section>
  )
}

function CamerasTile({ o }: { o: Overview }) {
  return (
    <Tile title="Cameras" action={<span className="font-mono text-[11px] text-muted-foreground">{o.workers_running} running</span>}>
      <ul className="space-y-2">
        {o.cameras.map(c => (
          <li key={c.id} className="flex items-center gap-2.5 text-[13px]">
            <span className="w-6 font-mono text-[11px] text-muted-foreground">{c.number ?? '—'}</span>
            <Dot on={c.online} />
            <span className="min-w-0 flex-1 truncate text-white">{c.name}</span>
            <span className="font-mono text-[11px] tabular text-muted-foreground">{c.online ? `${c.fps} fps` : c.enabled ? 'offline' : 'disabled'}</span>
          </li>
        ))}
      </ul>
    </Tile>
  )
}

function SummaryTile() {
  const { data, error } = useQuery({ queryKey: ['summary'], queryFn: fetchSummary, refetchInterval: 60_000 })
  return (
    <Tile title="Today">
      <ErrorLine error={error} />
      {data && (
        <>
          <p className="text-[13px] leading-relaxed text-white">{data.text}</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(data.incidents.by_type).map(([type, n]) => (
              <span key={type} className="rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                {type} {n}
              </span>
            ))}
          </div>
        </>
      )}
    </Tile>
  )
}

function TrendTile({ u }: { u: Usage }) {
  const data = u.incidents_by_day
  const max = Math.max(1, ...data.map(d => d.count))
  const W = 600
  const H = 140
  const pts = data.map((d, i) => [(i / Math.max(1, data.length - 1)) * W, H - 10 - (d.count / max) * (H - 30)] as const)
  const path = pts.map(([x, y], i) => `${i ? 'L' : 'M'} ${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  return (
    <Tile title="Incidents, last 30 days" className="sm:col-span-2" action={<span className="font-mono text-[11px] text-muted-foreground">{data.reduce((s, d) => s + d.count, 0)} total</span>}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-36 w-full" role="img" aria-label="Incidents per day over 30 days">
        <defs>
          <linearGradient id="trend-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={ACCENT} stopOpacity="0.28" />
            <stop offset="1" stopColor={ACCENT} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={`${path} L ${W} ${H} L 0 ${H} Z`} fill="url(#trend-fill)" />
        <path d={path} fill="none" stroke={ACCENT} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
      </svg>
    </Tile>
  )
}

function UsageTile({ u }: { u: Usage }) {
  const parts = [
    { label: 'Clips', value: u.clips.bytes, color: ACCENT },
    { label: 'Snapshots', value: u.snapshots.bytes, color: 'oklch(0.74 0.12 300)' },
    { label: 'Database', value: u.database_bytes ?? 0, color: 'oklch(0.78 0.04 280)' },
    { label: 'Logs', value: u.worker_logs.bytes + u.other_media.bytes, color: 'oklch(0.5 0.03 280)' },
  ]
  const total = parts.reduce((s, p) => s + p.value, 0) || 1
  const usedDisk = 1 - u.disk_free_bytes / Math.max(1, u.disk_total_bytes)
  return (
    <Tile title="Data usage" className="sm:col-span-2" action={<span className="font-mono text-[11px] text-muted-foreground">{bytes(u.disk_free_bytes)} free on disk</span>}>
      <p className="font-mono text-[22px] font-semibold tabular">{bytes(total)}</p>
      <div className="mt-3 flex h-8 gap-1">
        {parts.map(p => (
          <span key={p.label} className="rounded-[8px]" style={{ flexBasis: `${Math.max(2, (p.value / total) * 100)}%`, background: p.color }} title={`${p.label}: ${bytes(p.value)}`} />
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-4">
        {parts.map(p => (
          <li key={p.label} className="flex items-center gap-2 text-[12px]">
            <span className="size-2 rounded-full" style={{ background: p.color }} />
            <span className="text-muted-foreground">{p.label}</span>
            <span className="ml-auto font-mono tabular text-white">{bytes(p.value)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
        <Cpu className="h-3 w-3" /> {u.clips.count} clips · {u.snapshots.count} snapshots · disk {Math.round(usedDisk * 100)}% used
      </p>
    </Tile>
  )
}

export function DashboardPage() {
  const { user } = useAuth()
  const { can } = useMe()
  const overview = useQuery({ queryKey: ['overview'], queryFn: fetchOverview, refetchInterval: 10_000 })
  const usage = useQuery({ queryKey: ['usage'], queryFn: fetchUsage, enabled: can('view_audit'), refetchInterval: 60_000 })

  if (overview.error) return <ErrorLine error={overview.error} />
  if (!overview.data) return <p className="text-sm text-muted-foreground">Loading the dashboard…</p>
  const o = overview.data

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HeroTile o={o} name={user?.name ?? 'there'} />
        <ProblemsTile o={o} />
        <GuardsTile o={o} />
        <CamerasTile o={o} />
        <SummaryTile />
        {usage.data && <TrendTile u={usage.data} />}
        {usage.data && <UsageTile u={usage.data} />}
      </div>
    </div>
  )
}
