/**
 * pages/Duty.tsx
 * Guards and duty: who is on duty (called by the alarm ladder) and who is off (not called), their post and
 * equipment, check in or out by hand, each guard's QR pass, and the check-in history.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LogIn, LogOut, QrCode } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button, Dot, ErrorLine, Modal, Tile, timeAgo, TZ, useMe } from '@/components/admin/kit'
import { fetchDutyBoard, fetchDutyEvents, fetchDutyPass, setDuty, type DutyPerson } from '@/lib/admin'

const METHOD: Record<string, string> = { pass_scan: 'pass scanned', post_qr: 'post QR', dashboard: 'set on dashboard', voice: 'by voice' }

function PassModal({ person, onClose }: { person: DutyPerson; onClose: () => void }) {
  const { data, error } = useQuery({ queryKey: ['pass', person.user_id], queryFn: () => fetchDutyPass(person.user_id) })
  return (
    <Modal title={`Duty pass: ${person.name}`} onClose={onClose}>
      <ErrorLine error={error} />
      {data && (
        <div className="text-center">
          <img src={data.qr_png} alt={`QR duty pass for ${data.name}`} className="mx-auto h-56 w-56 rounded-xl bg-white p-2" />
          <p className="mt-3 font-mono text-sm text-white">{data.staff_id}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            The guard shows this on their phone; the control room scans it to check them in or out.
          </p>
        </div>
      )}
    </Modal>
  )
}

function PersonCard({ p, canSet, onPass }: { p: DutyPerson; canSet: boolean; onPass: () => void }) {
  const qc = useQueryClient()
  const change = useMutation({
    mutationFn: () => setDuty(p.user_id, p.on_duty ? 'out' : 'in'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['duty'] })
      qc.invalidateQueries({ queryKey: ['duty-events'] })
      qc.invalidateQueries({ queryKey: ['overview'] })
    },
  })
  return (
    <li className="rounded-2xl border border-border p-3">
      <div className="flex items-start gap-2.5">
        <Dot on={p.on_duty} className="mt-1.5" />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-white">{p.name} <span className="font-mono text-[11px] text-muted-foreground">{p.staff_id}</span></p>
          <p className="text-[11px] text-muted-foreground">
            {p.role}{p.post ? ` · post ${p.post}` : ''} · {p.since ? `${p.on_duty ? 'in' : 'out'} ${timeAgo(p.since)}${p.method ? ` (${METHOD[p.method] ?? p.method})` : ''}` : 'never checked in'}
          </p>
          {p.equipment && <p className="mt-1 text-[11px] text-muted-foreground">Equipment: {p.equipment}</p>}
        </div>
      </div>
      <div className="mt-2.5 flex gap-1.5">
        {canSet && (
          <Button onClick={() => change.mutate()} disabled={change.isPending}>
            {p.on_duty ? <><LogOut className="h-3 w-3" /> Check out</> : <><LogIn className="h-3 w-3" /> Check in</>}
          </Button>
        )}
        <Button onClick={onPass}><QrCode className="h-3 w-3" /> Pass</Button>
      </div>
      <ErrorLine error={change.error} />
    </li>
  )
}

export function DutyPage() {
  const { can } = useMe()
  const board = useQuery({ queryKey: ['duty'], queryFn: fetchDutyBoard, refetchInterval: 10_000 })
  const events = useQuery({ queryKey: ['duty-events'], queryFn: fetchDutyEvents, enabled: can('view_audit') || can('manage_guards') })
  const [pass, setPass] = useState<DutyPerson | null>(null)
  const on = board.data?.on_duty ?? []
  const off = board.data?.off_duty ?? []
  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader title="Guards and duty" description="On duty = called when an alarm happens. Off duty = not called (the shift roster is used only if nobody is on duty)." />
      <ErrorLine error={board.error} />
      <div className="grid gap-3 lg:grid-cols-2">
        <Tile title={<span className="text-white">On duty · {on.length}</span>}>
          <ul className="space-y-2">{on.map(p => <PersonCard key={p.user_id} p={p} canSet={can('manage_guards')} onPass={() => setPass(p)} />)}</ul>
          {on.length === 0 && <p className="text-sm" style={{ color: 'var(--high)' }}>Nobody is checked in: alarms go to the shift roster and the Head Guard is warned.</p>}
        </Tile>
        <Tile title={<span className="text-white">Off duty · {off.length}</span>}>
          <ul className="space-y-2">{off.map(p => <PersonCard key={p.user_id} p={p} canSet={can('manage_guards')} onPass={() => setPass(p)} />)}</ul>
        </Tile>
      </div>
      {events.data && (
        <Tile title="Check-in history" className="mt-3">
          <ul className="space-y-1.5 text-[13px]">
            {events.data.slice(0, 50).map((e, i) => (
              <li key={i} className="flex gap-3">
                <span className="w-24 shrink-0 font-mono text-[11px] text-muted-foreground">{new Date(e.at).toLocaleTimeString('en-IN', { timeZone: TZ, hour: '2-digit', minute: '2-digit' })}</span>
                <span className="text-white">{e.name}</span>
                <span style={{ color: e.action === 'in' ? 'var(--online)' : 'var(--neutral)' }}>{e.action === 'in' ? 'checked in' : 'checked out'}</span>
                <span className="text-muted-foreground">{METHOD[e.method] ?? e.method} · by {e.by}</span>
              </li>
            ))}
            {events.data.length === 0 && <li className="text-muted-foreground">No check-ins yet.</li>}
          </ul>
        </Tile>
      )}
      {pass && <PassModal person={pass} onClose={() => setPass(null)} />}
    </div>
  )
}
