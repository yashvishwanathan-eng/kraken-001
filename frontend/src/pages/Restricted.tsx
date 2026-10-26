/**
 * pages/Restricted.tsx
 * Restricted areas in one place (P35):
 *   - each camera: "Make restricted" turns the whole camera into a restricted area (one click, no drawing)
 *   - each restricted area: tick the people allowed in; there a recognised face raises no alert,
 *     anyone else (an unknown face, a face that cannot be seen, someone not ticked) raises an access alert
 *   - people: who has their face enrolled; "Add face" opens the same camera enrollment as People
 * Changes reach the camera within 30 s, no restart.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, LockOpen, ScanFace, ShieldAlert } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button, ErrorLine, Tile } from '@/components/admin/kit'
import { FaceAccessModal } from '@/components/admin/FaceAccessModal'
import {
  getRestricted, restrictCamera, setAllowedPeople, unrestrict,
  type RestrictedArea, type RestrictedOverview,
} from '@/lib/admin'
import { cn } from '@/lib/utils'

type PersonRow = RestrictedOverview['people'][number]

export function RestrictedPage() {
  const qc = useQueryClient()
  const data = useQuery({ queryKey: ['restricted'], queryFn: getRestricted })
  const [faceFor, setFaceFor] = useState<PersonRow | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['restricted'] })
  const restrict = useMutation({ mutationFn: restrictCamera, onSuccess: refresh })

  const people = data.data?.people ?? []
  const cameras = data.data?.cameras ?? []
  const restrictedCount = cameras.filter(c => c.areas.length > 0).length

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader
        title="Restricted areas"
        description="Only the people you tick may enter. Anyone else, an unknown face or a face the camera cannot see, raises an alert."
      />
      <ErrorLine error={data.error || restrict.error} />

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="grid content-start gap-4 md:grid-cols-2">
          {cameras.map(camera => (
            <Tile
              key={camera.id}
              title={<span className="text-white">{camera.number ? `${camera.number}. ` : ''}{camera.name}</span>}
              action={camera.areas.length > 0
                ? <span className="inline-flex items-center gap-1 rounded-full bg-[var(--critical-subtle)] px-2 py-0.5 font-mono text-[10px] text-[var(--critical)]"><Lock className="size-3" />RESTRICTED</span>
                : <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground"><LockOpen className="size-3" />OPEN</span>}
            >
              {camera.areas.length === 0 ? (
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs text-muted-foreground">Anyone may be here. Make it restricted to alert on everyone except the people you allow.</p>
                  <Button variant="primary" disabled={restrict.isPending} onClick={() => restrict.mutate(camera.id)}>
                    <Lock className="size-3.5" />Make restricted
                  </Button>
                </div>
              ) : (
                <div className="grid gap-4">
                  {camera.areas.map(area => (
                    <AreaEditor key={area.zone_id} area={area} people={people} onSaved={refresh} />
                  ))}
                </div>
              )}
            </Tile>
          ))}
          {data.isSuccess && cameras.length === 0 && <p className="text-sm text-muted-foreground">No cameras yet. Add one on the Cameras page.</p>}
        </div>

        <Tile title={`Faces (${people.filter(p => p.face_samples > 0).length} of ${people.length} enrolled)`} className="self-start">
          <p className="mb-3 text-[11px] text-muted-foreground">
            A person is let in only if the camera knows their face. {restrictedCount} restricted {restrictedCount === 1 ? 'camera' : 'cameras'}.
          </p>
          <ul className="grid gap-1.5">
            {people.map(p => (
              <li key={p.id} className="flex items-center justify-between gap-2 rounded-[12px] border border-border px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm text-white">{p.name}</p>
                  <p className={cn('font-mono text-[10px]', p.face_samples ? 'text-[var(--success,#22c55e)]' : 'text-muted-foreground')}>
                    {p.face_samples ? `face enrolled (${p.face_samples})` : 'no face yet'} · {p.role}
                  </p>
                </div>
                <Button onClick={() => setFaceFor(p)}><ScanFace className="size-3.5" />{p.face_samples ? 'Face' : 'Add face'}</Button>
              </li>
            ))}
          </ul>
        </Tile>
      </div>

      {faceFor && <FaceAccessModal person={faceFor} onClose={() => { setFaceFor(null); refresh() }} />}
    </div>
  )
}

function AreaEditor({ area, people, onSaved }: { area: RestrictedArea; people: PersonRow[]; onSaved: () => void }) {
  const [chosen, setChosen] = useState<number[] | null>(null)
  const allowed = chosen ?? area.allowed_user_ids
  const changed = chosen !== null && [...chosen].sort().join() !== [...area.allowed_user_ids].sort().join()
  const save = useMutation({
    mutationFn: () => setAllowedPeople(area.zone_id, allowed),
    onSuccess: () => { setChosen(null); onSaved() },
  })
  const remove = useMutation({ mutationFn: () => unrestrict(area.zone_id), onSuccess: onSaved })
  const noFace = people.filter(p => allowed.includes(p.id) && p.face_samples === 0)
  const toggle = (id: number) => setChosen(allowed.includes(id) ? allowed.filter(x => x !== id) : [...allowed, id])

  return (
    <div className="grid gap-2">
      <p className="text-[11px] text-muted-foreground">
        {area.whole_camera ? 'The whole camera' : `Area: ${area.name}`} · allowed in ({allowed.length}):
      </p>
      <div className="flex flex-wrap gap-1.5">
        {people.map(p => {
          const on = allowed.includes(p.id)
          return (
            <button
              key={p.id}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(p.id)}
              className={cn(
                'rounded-full border px-2.5 py-1 text-[11px] font-mono transition-colors cursor-pointer',
                on ? 'border-white bg-white text-black' : 'border-border text-muted-foreground hover:text-white',
              )}
            >
              {p.name}{p.face_samples === 0 ? ' ·no face' : ''}
            </button>
          )
        })}
      </div>
      {allowed.length === 0 && <p className="text-[11px] text-muted-foreground">Nobody ticked: everyone who enters raises an alert.</p>}
      {noFace.length > 0 && (
        <p className="flex items-center gap-1 text-[11px] text-[var(--high)]">
          <ShieldAlert className="size-3.5" />
          {noFace.map(p => p.name).join(', ')} {noFace.length === 1 ? 'has' : 'have'} no face enrolled, so the camera cannot let them in yet.
        </p>
      )}
      <ErrorLine error={save.error || remove.error} />
      <div className="flex justify-between gap-2">
        <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
          <LockOpen className="size-3.5" />Remove restriction
        </Button>
        <Button variant="primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
