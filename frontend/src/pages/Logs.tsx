/**
 * pages/Logs.tsx
 * Admin and Manager only (view_audit). Who used what: per-person totals, everything Friday was asked and
 * answered, who opened which camera, and every action (the audit log). The assistant does not learn from
 * use; this is a record of what it was asked and what it said.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/PageHeader'
import { ErrorLine, Tabs, Tile, timeAgo, TZ, useMe } from '@/components/admin/kit'
import { fetchActivity, fetchAuditLog } from '@/lib/admin'

type Tab = 'people' | 'ai' | 'views' | 'actions'

function clock(iso: string) {
  return new Date(iso).toLocaleString('en-IN', { timeZone: TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function LogsPage() {
  const { can } = useMe()
  const [tab, setTab] = useState<Tab>('people')
  const [hours, setHours] = useState(24)
  const activity = useQuery({ queryKey: ['activity', hours], queryFn: () => fetchActivity(hours), enabled: can('view_audit'), refetchInterval: 15_000 })
  const audit = useQuery({ queryKey: ['audit'], queryFn: fetchAuditLog, enabled: can('view_audit') && tab === 'actions' })

  if (!can('view_audit')) return <p className="text-sm text-muted-foreground">Only Admin and Manager can see the logs.</p>
  const a = activity.data

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader
        title="Logs"
        description="Every action, every question to Friday and every camera viewed, by whom and when"
        actions={
          <div className="flex items-center gap-2">
            <select
              aria-label="Time window"
              value={hours}
              onChange={e => setHours(Number(e.target.value))}
              className="rounded-[10px] border border-border bg-black px-2 py-1 text-xs text-white"
            >
              <option value={1}>Last hour</option>
              <option value={24}>Last 24 hours</option>
              <option value={168}>Last 7 days</option>
            </select>
            <Tabs value={tab} onChange={setTab} options={[
              { value: 'people', label: 'People' }, { value: 'ai', label: 'Friday' },
              { value: 'views', label: 'Camera views' }, { value: 'actions', label: 'All actions' },
            ]} />
          </div>
        }
      />
      <ErrorLine error={activity.error ?? audit.error} />

      {tab === 'people' && a && (
        <Tile className="p-0">
          <table className="w-full text-left text-[13px]">
            <thead className="text-[11px] text-muted-foreground">
              <tr className="border-b border-border">
                {['Person', 'Role', 'Actions', 'Friday questions', 'Friday commands', 'Camera views', 'Cameras', 'Last seen'].map(h => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {a.users.map(u => (
                <tr key={u.user} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-2.5 text-white">{u.user}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{u.role ?? '—'}</td>
                  <td className="px-4 py-2.5 font-mono">{u.actions}</td>
                  <td className="px-4 py-2.5 font-mono">{u.ai_questions}</td>
                  <td className="px-4 py-2.5 font-mono">{u.ai_commands}</td>
                  <td className="px-4 py-2.5 font-mono">{u.camera_views}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{u.cameras_viewed.join(', ') || '—'}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{timeAgo(u.last_at)}</td>
                </tr>
              ))}
              {a.users.length === 0 && <tr><td className="px-4 py-6 text-muted-foreground" colSpan={8}>Nothing in this window.</td></tr>}
            </tbody>
          </table>
        </Tile>
      )}

      {tab === 'ai' && a && (
        <div className="space-y-2">
          {a.ai.map((x, i) => (
            <Tile key={i} className="py-4">
              <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="font-mono">{clock(x.at)}</span>
                <span className="text-white">{x.user}</span>
                <span>· {x.kind}{x.intent ? ` · ${x.intent}` : ''}{x.status ? ` · ${x.status}` : ''} · {x.channel}</span>
              </p>
              <p className="mt-2 text-[13px] text-white">“{x.text}”</p>
              <p className="mt-1 text-[13px] text-muted-foreground">{x.answer}</p>
            </Tile>
          ))}
          {a.ai.length === 0 && <p className="text-sm text-muted-foreground">Nobody asked Friday anything in this window.</p>}
        </div>
      )}

      {tab === 'views' && a && (
        <Tile>
          <ul className="space-y-1.5 text-[13px]">
            {a.camera_views.map((v, i) => (
              <li key={i} className="flex gap-3">
                <span className="w-32 font-mono text-[11px] text-muted-foreground">{clock(v.at)}</span>
                <span className="text-white">{v.user}</span>
                <span className="text-muted-foreground">opened {v.camera_id}</span>
              </li>
            ))}
            {a.camera_views.length === 0 && <li className="text-muted-foreground">No camera opened in this window.</li>}
          </ul>
        </Tile>
      )}

      {tab === 'actions' && (
        <Tile className="p-0">
          <table className="w-full text-left text-[13px]">
            <thead className="text-[11px] text-muted-foreground">
              <tr className="border-b border-border">
                {['When', 'Who', 'Action', 'Target', 'Channel'].map(h => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {(audit.data ?? []).map((r, i) => (
                <tr key={i} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-2 font-mono text-[11px] text-muted-foreground">{clock(r.at)}</td>
                  <td className="px-4 py-2 text-white">{r.user}</td>
                  <td className="px-4 py-2">{r.action.replace(/_/g, ' ')}</td>
                  <td className="px-4 py-2 font-mono text-[11px] text-muted-foreground">{r.target ?? '—'}</td>
                  <td className="px-4 py-2 text-muted-foreground">{r.channel}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Tile>
      )}
    </div>
  )
}
