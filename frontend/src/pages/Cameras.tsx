/**
 * pages/Cameras.tsx
 * Every camera with its number, address and worker. Add a camera: test the address (one picture is read),
 * add it (numbered automatically, given to roles) and its worker starts. Start, stop, restart, disable, log.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera as CameraIcon, CheckCircle2, Play, Plus, RotateCcw, ScrollText, Square, XCircle } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button, Checks, Dot, ErrorLine, Field, Modal, Tile, timeAgo, useMe } from '@/components/admin/kit'
import {
  addCamera, cameraWorker, fetchCamerasAdmin, fetchRoles, fetchWorkerLog, testCamera, updateCamera,
  type CameraAdmin, type CameraTest,
} from '@/lib/admin'

function AddCameraModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: fetchRoles })
  const [name, setName] = useState('')
  const [source, setSource] = useState('http://')
  const [number, setNumber] = useState('')
  const [roleNames, setRoleNames] = useState<string[]>([])
  const [test, setTest] = useState<CameraTest | null>(null)
  const runTest = useMutation({ mutationFn: () => testCamera(source), onSuccess: setTest })
  const add = useMutation({
    mutationFn: () => addCamera({ name, source, number: number ? Number(number) : undefined, role_names: roleNames, start: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cameras-admin'] })
      qc.invalidateQueries({ queryKey: ['cameras'] })
      onClose()
    },
  })
  return (
    <Modal title="Add camera" onClose={onClose} wide>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" placeholder="Loading Bay" value={name} onChange={e => setName(e.target.value)} />
        <Field label="Number (optional)" type="number" min={1} value={number} onChange={e => setNumber(e.target.value)} hint="Empty = the next free number" />
        <div className="sm:col-span-2">
          <Field
            label="Address"
            value={source}
            onChange={e => {
              setSource(e.target.value)
              setTest(null)
            }}
            hint="Phone (IP Webcam): http://192.168.x.x:8080/video · laptop: http://IP:8090/video · CCTV: rtsp://… · this laptop's webcam: 0"
          />
        </div>
        <div className="flex items-center gap-2 sm:col-span-2">
          <Button onClick={() => runTest.mutate()} disabled={runTest.isPending || !source}>
            {runTest.isPending ? 'Testing…' : 'Test camera'}
          </Button>
          {test && (test.ok ? (
            <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--resolved)' }}>
              <CheckCircle2 className="h-3.5 w-3.5" /> Picture received: {test.width}×{test.height} in {test.ms} ms
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--critical)' }}>
              <XCircle className="h-3.5 w-3.5" /> {test.error}
            </span>
          ))}
          <ErrorLine error={runTest.error} />
        </div>
        <div className="sm:col-span-2">
          <Checks label="Give it to these roles" options={(roles ?? []).map(r => ({ value: r.name, label: r.name }))} value={roleNames} onChange={setRoleNames} />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2">
        <ErrorLine error={add.error} />
        <Button variant="primary" className="ml-auto" disabled={!name || !test?.ok || add.isPending} onClick={() => add.mutate()}>
          <Plus className="h-3.5 w-3.5" /> Add and start
        </Button>
      </div>
    </Modal>
  )
}

function LogModal({ camera, onClose }: { camera: CameraAdmin; onClose: () => void }) {
  const { data, error } = useQuery({ queryKey: ['worker-log', camera.id], queryFn: () => fetchWorkerLog(camera.id), refetchInterval: 3000 })
  return (
    <Modal title={`${camera.name}: worker messages`} onClose={onClose} wide>
      <ErrorLine error={error} />
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-[12px] bg-black p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {data?.lines.length ? data.lines.join('\n') : 'No messages yet (started by hand, or not started).'}
      </pre>
    </Modal>
  )
}

export function CamerasPage() {
  const qc = useQueryClient()
  const { can } = useMe()
  const { data, error } = useQuery({ queryKey: ['cameras-admin'], queryFn: fetchCamerasAdmin, refetchInterval: 5000, enabled: can('manage_cameras') })
  const [adding, setAdding] = useState(false)
  const [log, setLog] = useState<CameraAdmin | null>(null)
  const act = useMutation({
    mutationFn: ({ id, op }: { id: string; op: 'start' | 'stop' | 'restart' | 'enable' | 'disable' }) =>
      op === 'enable' || op === 'disable' ? updateCamera(id, { enabled: op === 'enable' }) : cameraWorker(id, op),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cameras-admin'] }),
  })

  if (!can('manage_cameras')) {
    return <p className="text-sm text-muted-foreground">Your role cannot manage cameras.</p>
  }
  const cams = data ?? []
  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader
        title="Cameras"
        description={`${cams.filter(c => c.online).length} of ${cams.length} online · ${cams.filter(c => c.worker.running).length} workers started from here`}
        actions={<Button variant="primary" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" /> Add camera</Button>}
      />
      <ErrorLine error={error ?? act.error} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cams.map(c => (
          <Tile
            key={c.id}
            title={<span className="flex items-center gap-2 text-white"><span className="font-mono text-muted-foreground">#{c.number ?? '—'}</span>{c.name}</span>}
            action={<span className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground"><Dot on={c.online} />{c.online ? `${c.fps} fps` : c.enabled ? 'offline' : 'disabled'}</span>}
          >
            <p className="truncate font-mono text-[11px] text-muted-foreground" title={c.source}>{c.source}</p>
            <dl className="mt-3 grid grid-cols-2 gap-y-1 text-[12px]">
              <dt className="text-muted-foreground">Worker</dt>
              <dd className="text-right">{c.worker.running ? `running · pid ${c.worker.pid}` : c.worker.managed ? `stopped${c.worker.exit_code != null ? ` (code ${c.worker.exit_code})` : ''}` : 'started by hand / not running'}</dd>
              <dt className="text-muted-foreground">Started</dt>
              <dd className="text-right">{timeAgo(c.worker.started_at)}</dd>
              <dt className="text-muted-foreground">Added by</dt>
              <dd className="text-right">{c.added_by ?? 'cameras.yaml'}</dd>
            </dl>
            <div className="mt-4 flex flex-wrap gap-1.5">
              {c.worker.running ? (
                <>
                  <Button onClick={() => act.mutate({ id: c.id, op: 'stop' })}><Square className="h-3 w-3" /> Stop</Button>
                  <Button onClick={() => act.mutate({ id: c.id, op: 'restart' })}><RotateCcw className="h-3 w-3" /> Restart</Button>
                </>
              ) : (
                <Button disabled={!c.enabled} onClick={() => act.mutate({ id: c.id, op: 'start' })}><Play className="h-3 w-3" /> Start</Button>
              )}
              <Button onClick={() => setLog(c)}><ScrollText className="h-3 w-3" /> Log</Button>
              <Button variant={c.enabled ? 'danger' : 'ghost'} className="ml-auto" onClick={() => act.mutate({ id: c.id, op: c.enabled ? 'disable' : 'enable' })}>
                {c.enabled ? 'Disable' : 'Enable'}
              </Button>
            </div>
          </Tile>
        ))}
        {cams.length === 0 && (
          <Tile><p className="flex items-center gap-2 text-sm text-muted-foreground"><CameraIcon className="h-4 w-4" /> No cameras yet. Add one.</p></Tile>
        )}
      </div>
      {adding && <AddCameraModal onClose={() => setAdding(false)} />}
      {log && <LogModal camera={log} onClose={() => setLog(null)} />}
    </div>
  )
}
