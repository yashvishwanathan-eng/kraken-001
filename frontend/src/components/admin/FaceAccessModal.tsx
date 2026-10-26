/**
 * components/admin/FaceAccessModal.tsx
 * Face recognition and restricted access for one person (P34):
 *   1. the person agrees (consent: faces are biometric data)
 *   2. the laptop camera takes 5 photos a moment apart; the server keeps only face fingerprints, never photos
 *   3. tick the restricted areas they may enter: there, a recognised face raises no alert
 *   "Delete face data" removes every fingerprint of this person.
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Trash2 } from 'lucide-react'
import { Button, Checks, ErrorLine, Modal } from '@/components/admin/kit'
import { deleteFace, enrollFace, getFace, getRestrictedZones, setAccess, type Person } from '@/lib/admin'

const SHOTS = 5

export function FaceAccessModal({ person, onClose }: { person: Pick<Person, 'id' | 'name'>; onClose: () => void }) {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['face', person.id], queryFn: () => getFace(person.id) })
  const zones = useQuery({ queryKey: ['restricted-zones'], queryFn: getRestrictedZones })
  const [allowed, setAllowed] = useState<string[] | null>(null)
  const [consent, setConsent] = useState(false)
  const [taking, setTaking] = useState(0)
  const [result, setResult] = useState<string | null>(null)
  const video = useRef<HTMLVideoElement>(null)
  const stream = useRef<MediaStream | null>(null)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const zoneIds = allowed ?? (status.data?.zone_ids ?? []).map(String)

  useEffect(() => {
    let alive = true
    navigator.mediaDevices?.getUserMedia({ video: { width: 640, height: 480 } })
      .then(s => {
        if (!alive) return s.getTracks().forEach(t => t.stop())
        stream.current = s
        if (video.current) video.current.srcObject = s
      })
      .catch(() => setCameraError('Camera blocked: allow the camera in the browser.'))
    return () => {
      alive = false
      stream.current?.getTracks().forEach(t => t.stop())
    }
  }, [])

  const refresh = () => qc.invalidateQueries({ queryKey: ['face', person.id] })
  const enroll = useMutation({
    mutationFn: async () => {
      const v = video.current!
      const canvas = document.createElement('canvas')
      canvas.width = v.videoWidth
      canvas.height = v.videoHeight
      const shots: Blob[] = []
      for (let i = 0; i < SHOTS; i++) {
        setTaking(i + 1)
        canvas.getContext('2d')!.drawImage(v, 0, 0)
        shots.push(await new Promise<Blob>(ok => canvas.toBlob(b => ok(b!), 'image/jpeg', 0.92)))
        await new Promise(r => setTimeout(r, 450)) // a moment apart: turn the head a little between shots
      }
      setTaking(0)
      return enrollFace(person.id, shots)
    },
    onSuccess: out => {
      setResult(`${out.added} of ${SHOTS} photos used.${out.rejected.length ? ` ${out.rejected.join('; ')}` : ''}`)
      refresh()
    },
    onError: () => setTaking(0),
  })
  const remove = useMutation({ mutationFn: () => deleteFace(person.id), onSuccess: () => { setResult('Face data deleted.'); refresh() } })
  const save = useMutation({ mutationFn: () => setAccess(person.id, zoneIds.map(Number)), onSuccess: () => { setResult('Access saved.'); refresh() } })

  const samples = status.data?.samples ?? 0
  return (
    <Modal title={`${person.name}: face and restricted access`} onClose={onClose} wide>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="relative overflow-hidden rounded-[14px] bg-black">
            <video ref={video} autoPlay muted playsInline className="aspect-[4/3] w-full -scale-x-100 object-cover" />
            {taking > 0 && <span className="absolute left-3 top-3 rounded-full bg-black/70 px-2 py-1 font-mono text-xs text-white">photo {taking} of {SHOTS}</span>}
            {cameraError && <p className="absolute inset-0 grid place-items-center p-4 text-center text-xs text-muted-foreground">{cameraError}</p>}
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">Face the camera, good light, close enough that the face fills about a third of the picture. Turn the head slightly between photos.</p>
        </div>
        <div className="flex flex-col gap-3 text-[13px]">
          <p>Enrolled: <span className="font-mono">{samples ? `${samples} face fingerprints` : 'not enrolled'}</span></p>
          <label className="flex items-start gap-2 text-[12px]">
            <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} className="mt-0.5" />
            <span>{person.name} agrees to face recognition for restricted areas. Only face fingerprints are kept, on this laptop, and can be deleted at any time.</span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={!consent || !!cameraError || enroll.isPending} onClick={() => enroll.mutate()}>
              <Camera className="h-3.5 w-3.5" /> {samples ? 'Add 5 more photos' : 'Enroll face (5 photos)'}
            </Button>
            {samples > 0 && <Button onClick={() => remove.mutate()} disabled={remove.isPending}><Trash2 className="h-3.5 w-3.5" /> Delete face data</Button>}
          </div>
          {zones.data && zones.data.length > 0 ? (
            <>
              <Checks label="May enter these restricted areas (no alert when their face is recognised)" value={zoneIds} onChange={setAllowed}
                      options={zones.data.map(z => ({ value: String(z.id), label: `${z.name.replace(/-/g, ' ')} · ${z.camera_id}` }))} />
              <div><Button onClick={() => save.mutate()} disabled={save.isPending}>Save access</Button></div>
            </>
          ) : <p className="text-[12px] text-muted-foreground">No restricted areas yet: draw one on a camera (kind "restricted").</p>}
          {result && <p className="text-[12px]" style={{ color: 'var(--resolved)' }}>{result}</p>}
          {[enroll.error, remove.error, save.error, status.error].map((e, i) => e ? <ErrorLine key={i} error={e} /> : null)}
        </div>
      </div>
    </Modal>
  )
}
