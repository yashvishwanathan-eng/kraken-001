/**
 * pages/Recordings.tsx
 * The all-day video of every camera: pick a camera and a day, then any 5-minute part of it.
 * Opened by Friday ("show the recordings of camera 2") and from an incident ("Full recording"):
 * /recordings?camera=cam-door&at=<ISO time> starts right at that moment.
 */
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Film } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { ErrorLine, Select, Tile, TZ } from '@/components/admin/kit'
import { RecordingPlayer } from '@/components/recordings/RecordingPlayer'
import { getRecordingAt, getRecordingDay, getRecordingDays } from '@/lib/admin'
import { fetchCameras } from '@/lib/api'
import { cn } from '@/lib/utils'

const time = (iso: string) => new Date(iso).toLocaleTimeString('en-IN', { timeZone: TZ, hour: '2-digit', minute: '2-digit' })
const istDay = (iso: string) => new Date(iso).toLocaleDateString('en-CA', { timeZone: TZ }) // YYYY-MM-DD

export function RecordingsPage() {
  const [params, setParams] = useSearchParams()
  const cameras = useQuery({ queryKey: ['cameras'], queryFn: fetchCameras })
  const cameraId = params.get('camera') || cameras.data?.[0]?.id || ''
  const at = params.get('at')
  const days = useQuery({ queryKey: ['rec-days', cameraId], queryFn: () => getRecordingDays(cameraId), enabled: !!cameraId })
  const [day, setDay] = useState<string>('')
  const shownDay = day || (at ? istDay(at) : days.data?.days[0] ?? '')
  const list = useQuery({
    queryKey: ['rec-day', cameraId, shownDay],
    queryFn: () => getRecordingDay(cameraId, shownDay || undefined),
    enabled: !!cameraId,
    refetchInterval: 60_000, // the file being written grows; new ones appear every 5 minutes
  })
  const jump = useQuery({ queryKey: ['rec-at', cameraId, at], queryFn: () => getRecordingAt(cameraId, at!), enabled: !!cameraId && !!at, retry: false })
  const [chosen, setChosen] = useState<{ url: string; startAt: number } | null>(null)

  useEffect(() => {
    if (jump.data) setChosen({ url: jump.data.url, startAt: Math.max(0, jump.data.offset_s - 10) })
  }, [jump.data])

  const segments = useMemo(() => list.data?.segments ?? [], [list.data])
  const index = chosen ? segments.findIndex(s => s.url === chosen.url) : -1
  const playNext = () => {
    const next = segments[index + 1]
    if (next) setChosen({ url: next.url, startAt: 0 })
  }

  return (
    <div className="mx-auto w-full max-w-[1180px]">
      <PageHeader title="Recordings" description="Every camera, recorded all day in 5-minute parts. Kept for 7 days." />
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Select label="Camera" value={cameraId} onChange={e => { setDay(''); setChosen(null); setParams({ camera: e.target.value }) }}
                  options={(cameras.data ?? []).map(c => ({ value: c.id, label: c.name }))} />
        </div>
        <div className="w-44">
          <Select label="Day" value={shownDay} onChange={e => { setDay(e.target.value); setChosen(null) }}
                  options={(days.data?.days ?? []).map(d => ({ value: d, label: d }))} />
        </div>
      </div>
      {jump.error ? <p className="mb-3 text-xs text-muted-foreground">No recording covers that moment: the camera may have been off, or it is older than 7 days.</p> : null}
      <div className="grid gap-3 lg:grid-cols-3">
        <Tile title="Player" className="lg:col-span-2">
          {chosen ? <RecordingPlayer url={chosen.url} startAt={chosen.startAt} onEnded={playNext} /> : (
            <div className="grid aspect-video place-items-center rounded-[14px] bg-black text-sm text-muted-foreground">
              <span className="flex items-center gap-2"><Film className="h-4 w-4" aria-hidden="true" /> Pick a time on the right</span>
            </div>
          )}
        </Tile>
        <Tile title={shownDay ? `Parts on ${shownDay}` : 'Parts'}>
          {list.error ? <ErrorLine error={list.error} /> : null}
          {!segments.length && !list.isLoading ? <p className="text-xs text-muted-foreground">No recordings for this day yet. A camera records while it is running.</p> : null}
          <ol className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
            {segments.map(s => (
              <li key={s.url}>
                <button type="button" onClick={() => setChosen({ url: s.url, startAt: 0 })}
                        className={cn('flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[13px] hover:bg-white/5 cursor-pointer',
                          chosen?.url === s.url ? 'bg-white/10 text-white' : 'text-muted-foreground')}>
                  <span className="font-mono">{time(s.start)} – {time(s.end)}</span>
                  <span className="font-mono text-[11px]">{s.size_mb} MB</span>
                </button>
              </li>
            ))}
          </ol>
        </Tile>
      </div>
    </div>
  )
}
