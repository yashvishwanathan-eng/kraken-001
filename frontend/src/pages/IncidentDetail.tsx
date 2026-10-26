/**
 * pages/IncidentDetail.tsx
 * One incident: snapshot, 10 s clip, what happened step by step (detection, calls, WhatsApp, escalation,
 * acknowledgement), and Acknowledge / Resolve / False alarm. Updates live from the WebSocket.
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageHeader } from '@/components/PageHeader'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusChip } from '@/components/StatusChip'
import { Button, ErrorLine, Tile, TZ } from '@/components/admin/kit'
import { RecordingPlayer } from '@/components/recordings/RecordingPlayer'
import { getRecordingAt } from '@/lib/admin'
import { acknowledgeEvent, fetchEvent, resolveEvent } from '@/lib/api'
import { describe } from '@/lib/timeline'
import { wsClient } from '@/lib/ws'
import type { Incident } from '@/lib/types'

export function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const incidentId = Number(id)
  const qc = useQueryClient()
  const { data, error } = useQuery({ queryKey: ['incident', incidentId], queryFn: () => fetchEvent(incidentId), enabled: Number.isFinite(incidentId) })
  const [live, setLive] = useState<Incident | null>(null)
  const incident = live && data && live.timeline.length >= data.timeline.length ? live : data
  const full = useQuery({
    queryKey: ['incident-recording', incidentId, incident?.camera_id, incident?.started_at],
    queryFn: () => getRecordingAt(incident!.camera_id, incident!.started_at),
    enabled: !!incident,
    retry: false,
  })

  useEffect(() => wsClient.subscribe(event => {
    const inc = (event && typeof event === 'object' && 'timeline' in event ? event : null) as Incident | null
    if (inc && inc.id === incidentId) setLive(inc)
  }), [incidentId])

  const act = useMutation({
    mutationFn: (what: 'ack' | 'resolved' | 'false_alarm') => (what === 'ack' ? acknowledgeEvent(incidentId) : resolveEvent(incidentId, what)),
    onSuccess: inc => {
      setLive(inc)
      qc.invalidateQueries({ queryKey: ['incident', incidentId] })
    },
  })

  if (error) return <ErrorLine error={error} />
  if (!incident) return <p className="text-sm text-muted-foreground">Loading incident…</p>
  const started = Date.parse(incident.started_at)

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader
        title={`Incident #${incident.id}: ${incident.type}`}
        description={`${incident.camera_name}${incident.zone ? ` · ${incident.zone.replace(/-/g, ' ')}` : ''} · ${new Date(incident.started_at).toLocaleString('en-IN', { timeZone: TZ })}`}
        actions={<div className="flex items-center gap-2"><SeverityBadge severity={incident.severity} /><StatusChip status={incident.status} /></div>}
      />
      <div className="grid gap-3 lg:grid-cols-3">
        <Tile title="Clip and snapshot" className="lg:col-span-2">
          <div className="grid gap-3 sm:grid-cols-2">
            {incident.clip_url ? (
              <video src={incident.clip_url} controls className="w-full rounded-[14px] bg-black" poster={incident.snapshot_url || undefined} />
            ) : <div className="grid aspect-video place-items-center rounded-[14px] bg-black text-xs text-muted-foreground">No clip (demo alarm or still saving)</div>}
            {incident.snapshot_url ? (
              <img src={incident.snapshot_url} alt="Snapshot at detection" className="w-full rounded-[14px] bg-black" />
            ) : <div className="grid aspect-video place-items-center rounded-[14px] bg-black text-xs text-muted-foreground">No snapshot</div>}
          </div>
          <p className="mt-3 font-mono text-[11px] text-muted-foreground">Confidence {(incident.confidence * 100).toFixed(0)}%{incident.acknowledged_by ? ` · acknowledged by ${incident.acknowledged_by}` : ''}</p>
        </Tile>
        <Tile title="Full recording" className="lg:col-span-2"
              action={<Link to={`/recordings?camera=${encodeURIComponent(incident.camera_id)}&at=${encodeURIComponent(incident.started_at)}`} className="text-xs text-muted-foreground hover:text-white">Open in Recordings</Link>}>
          {full.data
            ? <RecordingPlayer url={full.data.url} startAt={Math.max(0, full.data.offset_s - 10)} autoPlay={false} />
            : <div className="grid aspect-video place-items-center rounded-[14px] bg-black text-xs text-muted-foreground">
                {full.isLoading ? 'Finding the recording…' : 'No all-day recording covers this moment (the camera was not recording then).'}
              </div>}
          <p className="mt-2 text-[11px] text-muted-foreground">Starts 10 s before the incident; play on for the whole 5-minute part.</p>
        </Tile>
        <Tile title="What happened">
          <ol className="space-y-2">
            {incident.timeline.map((e, i) => {
              const { text, icon: Icon, tone } = describe(e)
              return (
                <li key={i} className="flex items-start gap-2.5 text-[13px]">
                  <span className="w-14 shrink-0 text-right font-mono text-[11px] text-muted-foreground">+{Math.max(0, (Date.parse(e.at) - started) / 1000).toFixed(1)}s</span>
                  <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: tone }} aria-hidden="true" />
                  <span className="leading-snug text-white">{text}</span>
                </li>
              )
            })}
          </ol>
          <div className="mt-auto flex flex-wrap gap-1.5 pt-5">
            {incident.status === 'open' && <Button variant="primary" onClick={() => act.mutate('ack')}>Acknowledge</Button>}
            {(incident.status === 'open' || incident.status === 'acknowledged') && (
              <>
                <Button onClick={() => act.mutate('resolved')}>Resolve</Button>
                <Button variant="danger" onClick={() => act.mutate('false_alarm')}>False alarm</Button>
              </>
            )}
          </div>
          <ErrorLine error={act.error} />
        </Tile>
      </div>
    </div>
  )
}
