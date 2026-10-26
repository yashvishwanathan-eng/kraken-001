/**
 * pages/Conversations.tsx
 * Every talk with Friday, kept as a chat: the phone calls she made (with the whole call's audio) and what
 * people asked her on the laptop (with their voice). Separate from Friday's page and from the Logs.
 * Refreshes by itself when a new line is said (a small "conversation" notice on the WebSocket).
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Laptop, Phone, Play } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { ErrorLine, Tabs, Tile, TZ, timeAgo } from '@/components/admin/kit'
import { getConversation, getConversationPeople, getConversations, mediaUrl } from '@/lib/admin'
import { wsClient } from '@/lib/ws'
import { cn } from '@/lib/utils'

type Filter = 'all' | 'phone' | 'laptop' | 'people'
const clock = (iso: string) => new Date(iso).toLocaleTimeString('en-IN', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit' })
// how a call ended, by name: answered / declined / not answered
const OUTCOME: Record<string, { label: string; color: string }> = {
  answered: { label: 'Answered', color: 'var(--resolved)' },
  declined: { label: 'Declined', color: 'var(--critical)' },
  no_answer: { label: 'No answer', color: 'var(--high)' },
  failed: { label: 'Failed', color: 'var(--critical)' },
  cancelled: { label: 'Cancelled', color: 'var(--neutral)' },
}
const length = (s: number | null) => (s == null ? '' : s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`)

function OutcomeBadge({ outcome, duration }: { outcome: string | null; duration?: number | null }) {
  if (!outcome || !OUTCOME[outcome]) return null
  const o = OUTCOME[outcome]
  return (
    <span className="rounded-full border px-2 py-0.5 font-mono text-[10px]" style={{ color: o.color, borderColor: o.color }}>
      {o.label}{outcome === 'answered' && duration ? ` · ${length(duration)}` : ''}
    </span>
  )
}

const ACTION_TEXT: Record<string, string> = {
  ack: 'marked as going', false_alarm: 'marked false alarm', alert_all: 'alerted all guards', responding: 'marked responding',
  safe: 'marked safe', coming: 'coming', end: 'ended the call',
}

export function ConversationsPage() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<Filter>('all')
  const [openId, setOpenId] = useState<number | null>(null)
  const [person, setPerson] = useState<string | null>(null)
  const people = useQuery({ queryKey: ['conversation-people'], queryFn: getConversationPeople, enabled: filter === 'people' })
  const list = useQuery({
    queryKey: ['conversations', filter, person],
    queryFn: () => getConversations(filter === 'phone' || filter === 'laptop' ? filter : undefined, filter === 'people' ? person ?? undefined : undefined),
    enabled: filter !== 'people' || !!person,
  })
  const shownId = openId ?? list.data?.[0]?.id ?? null
  const one = useQuery({ queryKey: ['conversation', shownId], queryFn: () => getConversation(shownId!), enabled: shownId != null })

  useEffect(() => wsClient.subscribe(event => {
    if ((event as { kind?: string } | null)?.kind !== 'conversation') return
    qc.invalidateQueries({ queryKey: ['conversations'] })
    qc.invalidateQueries({ queryKey: ['conversation-people'] })
    qc.invalidateQueries({ queryKey: ['conversation'] })
  }), [qc])

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader title="Conversations" description="Every talk with Friday: the calls she made and what people asked her on the laptop, with the audio." />
      <div className="mb-3">
        <Tabs value={filter} onChange={v => { setFilter(v); setOpenId(null); setPerson(null) }}
              options={[{ value: 'all', label: 'All' }, { value: 'phone', label: 'Phone calls' }, { value: 'laptop', label: 'Laptop' }, { value: 'people', label: 'By person' }]} />
      </div>
      <div className="grid gap-3 lg:grid-cols-[340px_1fr]">
        <Tile title={filter === 'people' ? (person ? `${person}'s talks` : 'People') : 'Talks'}
              action={filter === 'people' && person ? <button type="button" onClick={() => { setPerson(null); setOpenId(null) }} className="text-xs text-muted-foreground hover:text-white cursor-pointer">All people</button> : null}>
          {filter === 'people' && !person ? (
            <ol className="max-h-[68vh] space-y-1 overflow-y-auto pr-1">
              {(people.data ?? []).map(p => (
                <li key={p.person}>
                  <button type="button" onClick={() => setPerson(p.person)} className="w-full rounded-lg px-3 py-2 text-left hover:bg-white/5 cursor-pointer">
                    <span className="flex items-center text-[13px] font-medium text-white">{p.person}<span className="ml-auto font-mono text-[10px] text-muted-foreground">{timeAgo(p.last_at)}</span></span>
                    <span className="mt-0.5 block font-mono text-[11px] text-muted-foreground">
                      {p.calls} calls · <span style={{ color: 'var(--resolved)' }}>{p.answered} answered</span> · <span style={{ color: 'var(--critical)' }}>{p.declined} declined</span> · <span style={{ color: 'var(--high)' }}>{p.no_answer} no answer</span>{p.laptop_talks ? ` · ${p.laptop_talks} laptop` : ''}
                    </span>
                  </button>
                </li>
              ))}
              {people.data && !people.data.length ? <p className="text-xs text-muted-foreground">Nobody yet.</p> : null}
            </ol>
          ) : null}
          {list.error ? <ErrorLine error={list.error} /> : null}
          {list.data && !list.data.length ? <p className="text-xs text-muted-foreground">Nothing yet. Calls and laptop talks appear here as they happen.</p> : null}
          <ol className={cn('max-h-[68vh] space-y-1 overflow-y-auto pr-1', filter === 'people' && !person && 'hidden')}>
            {(list.data ?? []).map(c => {
              const Icon = c.channel === 'phone' ? Phone : Laptop
              return (
                <li key={c.id}>
                  <button type="button" onClick={() => setOpenId(c.id)}
                          className={cn('w-full rounded-lg px-3 py-2 text-left hover:bg-white/5 cursor-pointer', shownId === c.id && 'bg-white/10')}>
                    <span className="flex items-center gap-2 text-[13px] text-white">
                      <Icon className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                      <span className="font-medium">{c.person}</span>
                      <OutcomeBadge outcome={c.outcome} />
                      <span className="ml-auto font-mono text-[10px] text-muted-foreground">{timeAgo(c.last_at)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-[12px] text-muted-foreground">{c.last_text}</span>
                  </button>
                </li>
              )
            })}
          </ol>
        </Tile>
        <Tile title={one.data ? `${one.data.channel === 'phone' ? 'Call with' : 'Laptop talk with'} ${one.data.person}` : 'Conversation'}
              action={one.data ? <span className="flex items-center gap-2"><OutcomeBadge outcome={one.data.outcome} duration={one.data.duration_s} />{one.data.incident_id ? <Link to={`/incidents/${one.data.incident_id}`} className="text-xs text-muted-foreground hover:text-white">Incident #{one.data.incident_id}</Link> : null}</span> : null}>
          {one.data?.recording_url ? (
            <div className="mb-3">
              <p className="mb-1 text-[11px] text-muted-foreground">Whole call recording</p>
              <audio controls src={mediaUrl(one.data.recording_url)} className="w-full" />
            </div>
          ) : one.data?.channel === 'phone' ? <p className="mb-3 text-[11px] text-muted-foreground">The call recording appears here a minute after the call ends.</p> : null}
          <ol className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
            {(one.data?.lines ?? []).map((line, i) => {
              const friday = line.speaker === 'friday'
              return (
                <li key={i} className={cn('flex', friday ? 'justify-start' : 'justify-end')}>
                  <div className={cn('max-w-[80%] rounded-2xl px-3.5 py-2 text-[13px] leading-snug',
                    friday ? 'rounded-bl-sm bg-sky-500/15 text-sky-50' : 'rounded-br-sm bg-white/10 text-white')}>
                    <p className="mb-0.5 font-mono text-[10px] text-muted-foreground">
                      {friday ? 'Friday' : one.data?.person} · {clock(line.at)}
                      {friday && line.action && ACTION_TEXT[line.action] ? ` · ${ACTION_TEXT[line.action]}` : ''}
                      {!friday && line.action && ['voice', 'typed', 'wake word'].includes(line.action) ? ` · ${line.action}` : ''}
                    </p>
                    <p>{line.text}</p>
                    {line.audio_url ? (
                      <button type="button" onClick={() => void new Audio(mediaUrl(line.audio_url!)).play()}
                              className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-white cursor-pointer">
                        <Play className="h-3 w-3" aria-hidden="true" /> Play their voice
                      </button>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ol>
        </Tile>
      </div>
    </div>
  )
}
