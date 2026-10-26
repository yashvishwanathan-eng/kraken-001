/**
 * pages/People.tsx
 * Everyone who uses RingGuard, with their staff ID, role, cameras and phone (every person is on the call list).
 * Add person: sends the invitation email with link and QR, and puts their number on the call list.
 * Roles tab: create roles and give them cameras; changes reach everyone with that role.
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Phone, Plus, Shield, UserPlus } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button, Checks, Dot, ErrorLine, Field, Modal, Select, Tabs, Tile, useMe } from '@/components/admin/kit'
import {
  createRole, fetchPeople, fetchRoles, invitePerson, updatePerson, updateRole,
  type InviteCreated, type Person, type Role,
} from '@/lib/admin'
import { fetchCameras } from '@/lib/api'

const ACTION_LABELS: Record<string, string> = {
  all_cameras: 'All cameras', ack: 'Acknowledge', resolve: 'Resolve', manage_users: 'Manage people', view_audit: 'View logs',
  manage_zones: 'Zones', manage_responders: 'Call roster', alert_all: 'Alert everybody', broadcast: 'Broadcast',
  assistant: 'Friday', manage_sites: 'Sites', manage_guards: 'Guards and duty', view_guard_details: 'Guard details',
  scan_badges: 'Scan badges', delegate_cameras: 'Delegate', manage_cameras: 'Cameras', run_demo: 'Demo alarms',
}
const PHONE_HINT = 'With country code, e.g. +919876543210. They are called with this number.'

function useCameraOptions() {
  const { data } = useQuery({ queryKey: ['cameras'], queryFn: fetchCameras })
  return (data ?? []).map(c => ({ value: c.id, label: c.name }))
}

function AddPersonModal({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const qc = useQueryClient()
  const cameraOptions = useCameraOptions()
  const [form, setForm] = useState({ name: '', email: '', phone: '+91', role: roles[0]?.name ?? 'Guard', zone: 'general', shift_start: '00:00', shift_end: '00:00' })
  const role = roles.find(r => r.name === form.role)
  const [cameras, setCameras] = useState<string[] | null>(null) // null = the role's cameras
  const [done, setDone] = useState<InviteCreated | null>(null)
  const invite = useMutation({
    mutationFn: () => invitePerson({ ...form, camera_ids: cameras ?? undefined }),
    onSuccess: out => {
      setDone(out)
      qc.invalidateQueries({ queryKey: ['people'] })
    },
  })
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value })

  if (done) {
    return (
      <Modal title={`${done.name} invited`} onClose={onClose}>
        <p className="text-sm text-white">Staff ID <span className="font-mono">{done.staff_id}</span></p>
        <p className="mt-1 text-xs text-muted-foreground">
          Email: {done.email_status === 'sent' ? 'sent' : done.email_status === 'simulated' ? 'simulated (email is in simulate mode)' : 'not sent: share the QR or link below'}
        </p>
        <img src={done.qr_png} alt="Invitation QR code" className="mx-auto mt-4 h-48 w-48 rounded-xl bg-white p-2" />
        <p className="mt-3 break-all rounded-[10px] bg-black p-2 font-mono text-[11px] text-muted-foreground">{done.link}</p>
        <p className="mt-3 text-xs text-muted-foreground">
          Their phone is on the call list. They can be called as soon as they accept and set their PIN.
        </p>
        <div className="mt-4 flex justify-end"><Button variant="primary" onClick={onClose}>Done</Button></div>
      </Modal>
    )
  }

  return (
    <Modal title="Add person" onClose={onClose} wide>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={e => {
          e.preventDefault()
          invite.mutate()
        }}
      >
        <Field label="Name" required value={form.name} onChange={set('name')} />
        <Field label="Email (the invitation goes here)" type="email" required value={form.email} onChange={set('email')} />
        <Field label="Phone" required value={form.phone} onChange={set('phone')} pattern="^\+[1-9]\d{7,14}$" hint={PHONE_HINT} />
        <Select label="Role" value={form.role} onChange={set('role')} options={roles.map(r => ({ value: r.name, label: r.name }))} />
        <Field label="Call zone" value={form.zone} onChange={set('zone')} hint="Used by 'call the guards of <zone>'" />
        <div className="grid grid-cols-2 gap-2">
          <Field label="Shift start" type="time" value={form.shift_start} onChange={set('shift_start')} />
          <Field label="Shift end" type="time" value={form.shift_end} onChange={set('shift_end')} hint="00:00 to 00:00 = all day" />
        </div>
        <div className="sm:col-span-2">
          <Checks label={`Cameras${cameras === null ? ` (from the role ${form.role})` : ''}`} options={cameraOptions}
                  value={cameras ?? role?.camera_ids ?? []} onChange={setCameras} />
        </div>
        <div className="flex items-center justify-between gap-3 sm:col-span-2">
          <ErrorLine error={invite.error} />
          <Button type="submit" variant="primary" disabled={invite.isPending} className="ml-auto">
            <UserPlus className="h-3.5 w-3.5" /> Send invitation
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function EditPersonModal({ person, roles, onClose }: { person: Person; roles: Role[]; onClose: () => void }) {
  const qc = useQueryClient()
  const cameraOptions = useCameraOptions()
  const [form, setForm] = useState({
    phone: person.phone ?? '+91', role: person.role, zone: person.zone ?? 'general',
    shift_start: person.shift_start ?? '00:00', shift_end: person.shift_end ?? '00:00',
  })
  const [cameras, setCameras] = useState(person.camera_ids)
  const save = useMutation({
    mutationFn: (extra: { active?: boolean }) => updatePerson(person.id, { ...form, camera_ids: cameras, ...extra }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['people'] })
      onClose()
    },
  })
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value })
  return (
    <Modal title={`${person.name} · ${person.staff_id}`} onClose={onClose} wide>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Phone" value={form.phone} onChange={set('phone')} hint={PHONE_HINT} />
        <Select label="Role" value={form.role} onChange={set('role')}
                options={[...new Set([form.role, ...roles.map(r => r.name)])].map(r => ({ value: r, label: r }))} />
        <Field label="Call zone" value={form.zone} onChange={set('zone')} />
        <div className="grid grid-cols-2 gap-2">
          <Field label="Shift start" type="time" value={form.shift_start} onChange={set('shift_start')} />
          <Field label="Shift end" type="time" value={form.shift_end} onChange={set('shift_end')} />
        </div>
        <div className="sm:col-span-2">
          <Checks label="Cameras" options={cameraOptions} value={cameras} onChange={setCameras} />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2">
        <ErrorLine error={save.error} />
        <Button variant="danger" className="ml-auto" onClick={() => save.mutate({ active: !person.active })}>
          {person.active ? 'Deactivate' : 'Activate'}
        </Button>
        <Button variant="primary" onClick={() => save.mutate({})} disabled={save.isPending}>Save</Button>
      </div>
    </Modal>
  )
}

function PeopleTab({ roles }: { roles: Role[] }) {
  const { data, error } = useQuery({ queryKey: ['people'], queryFn: fetchPeople })
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)
  const [query, setQuery] = useState('')
  const list = useMemo(
    () => (data ?? []).filter(p => `${p.name} ${p.role} ${p.staff_id} ${p.phone ?? ''}`.toLowerCase().includes(query.toLowerCase())),
    [data, query],
  )
  const missingPhone = (data ?? []).filter(p => p.active && !p.phone).length
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search name, role, staff ID, phone"
          aria-label="Search people"
          className="w-64 rounded-[10px] border border-border bg-black px-2.5 py-1.5 text-sm text-white placeholder:text-white/30 focus:outline-none"
        />
        {missingPhone > 0 && (
          <span className="rounded-full px-2.5 py-1 text-[11px]" style={{ background: 'var(--high-subtle)', color: 'var(--high)' }}>
            {missingPhone} {missingPhone === 1 ? 'person has' : 'people have'} no phone: they cannot be called
          </span>
        )}
        <Button variant="primary" className="ml-auto" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" /> Add person</Button>
      </div>
      <ErrorLine error={error} />
      <Tile className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="text-[11px] text-muted-foreground">
              <tr className="border-b border-border">
                {['Name', 'Staff ID', 'Role', 'Phone', 'Zone · shift', 'Cameras', ''].map(h => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {list.map(p => (
                <tr key={p.id} className="border-b border-border/60 last:border-0 hover:bg-white/[0.03]">
                  <td className="px-4 py-2.5"><span className="flex items-center gap-2"><Dot on={p.active} />{p.name}</span></td>
                  <td className="px-4 py-2.5 font-mono text-muted-foreground">{p.staff_id}</td>
                  <td className="px-4 py-2.5">{p.role}</td>
                  <td className="px-4 py-2.5 font-mono">
                    {p.phone ?? <span style={{ color: 'var(--high)' }}>missing</span>}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{p.zone ?? '—'} · {p.shift_start === p.shift_end ? 'all day' : `${p.shift_start}–${p.shift_end}`}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{p.actions.includes('all_cameras') ? 'all' : p.camera_ids.length}</td>
                  <td className="px-4 py-2.5 text-right"><Button onClick={() => setEditing(p)}>Edit</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Tile>
      {adding && <AddPersonModal roles={roles} onClose={() => setAdding(false)} />}
      {editing && <EditPersonModal person={editing} roles={roles} onClose={() => setEditing(null)} />}
    </>
  )
}

function RoleModal({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const qc = useQueryClient()
  const cameraOptions = useCameraOptions()
  const [name, setName] = useState(role?.name ?? '')
  const [cameras, setCameras] = useState(role?.camera_ids ?? [])
  const [actions, setActions] = useState(role?.actions ?? ['ack'])
  const save = useMutation({
    mutationFn: () => (role ? updateRole(role.id, { name, camera_ids: cameras, actions }) : createRole({ name, camera_ids: cameras, actions })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roles'] })
      qc.invalidateQueries({ queryKey: ['people'] })
      onClose()
    },
  })
  return (
    <Modal title={role ? `Role: ${role.name}` : 'New role'} onClose={onClose} wide>
      <div className="grid gap-3">
        <Field label="Name" value={name} onChange={e => setName(e.target.value)} />
        <Checks label="Cameras" options={cameraOptions} value={cameras} onChange={setCameras} />
        <Checks label="What they can do" options={Object.entries(ACTION_LABELS).map(([value, label]) => ({ value, label }))} value={actions} onChange={setActions} />
        {role && role.users > 0 && (
          <p className="text-[11px] text-muted-foreground">Saving updates the {role.users} {role.users === 1 ? 'person' : 'people'} with this role.</p>
        )}
      </div>
      <div className="mt-4 flex items-center gap-2">
        <ErrorLine error={save.error} />
        <Button variant="primary" className="ml-auto" disabled={!name || save.isPending} onClick={() => save.mutate()}>Save role</Button>
      </div>
    </Modal>
  )
}

function RolesTab({ roles }: { roles: Role[] }) {
  const [editing, setEditing] = useState<Role | null | 'new'>(null)
  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button variant="primary" onClick={() => setEditing('new')}><Plus className="h-3.5 w-3.5" /> New role</Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {roles.map(r => (
          <Tile key={r.id} title={<span className="flex items-center gap-2 text-white"><Shield className="h-3.5 w-3.5" />{r.name}</span>}
                action={<Button onClick={() => setEditing(r)}>Edit</Button>}>
            <p className="text-xs text-muted-foreground">{r.users} {r.users === 1 ? 'person' : 'people'} · {r.camera_ids.length} cameras</p>
            <div className="mt-3 flex flex-wrap gap-1">
              {r.actions.map(a => (
                <span key={a} className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{ACTION_LABELS[a] ?? a}</span>
              ))}
            </div>
          </Tile>
        ))}
      </div>
      {editing && <RoleModal role={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </>
  )
}

export function PeoplePage() {
  const [tab, setTab] = useState<'people' | 'roles'>('people')
  const { can } = useMe()
  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: fetchRoles })
  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader
        title="People"
        description="Everyone on RingGuard: staff ID, role, cameras and the phone they are called on"
        actions={<Tabs value={tab} onChange={setTab} options={[{ value: 'people', label: 'People' }, { value: 'roles', label: 'Roles' }]} />}
      />
      {!can('manage_users') && !can('delegate_cameras') ? (
        <p className="text-sm text-muted-foreground">Your role cannot manage people.</p>
      ) : tab === 'people' ? (
        <PeopleTab roles={roles ?? []} />
      ) : (
        <RolesTab roles={roles ?? []} />
      )}
      <p className="mt-4 flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
        <Phone className="h-3 w-3" /> Numbers are stored here and dialled through Twilio. On a Twilio trial, verify each number once in the Twilio console.
      </p>
    </div>
  )
}
