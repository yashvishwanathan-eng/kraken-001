/**
 * components/live/CameraFocus.tsx
 * One camera filling the screen (Live page: click a tile, or Friday's "show camera 4" / "show me the fire").
 *   - the stream as large as it fits, never cropped
 *   - header: number, name, live FPS, zones; F = the browser's real full screen, Esc = back to the grid
 *   - left / right arrows (or the side buttons) go to the previous / next camera
 *   - side panel: this camera's latest incidents (click one for its page), Recordings, Restricted areas
 *   - the frame glows in the alarm colour while an open critical incident is on this camera
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Activity, ChevronLeft, ChevronRight, Eye, Film, Lock, Maximize2, Minimize2, Video, X } from 'lucide-react'
import { CameraStream } from '@/components/CameraStream'
import { incidentRgb, typeLabel } from '@/lib/incidentLook'
import { formatTime } from '@/lib/time'
import type { Camera, Incident } from '@/lib/types'

interface Props {
  camera: Camera
  cameras: Camera[]
  incidents: Incident[]
  onChange: (cameraId: string) => void
  onClose: () => void
}

export function CameraFocus({ camera, cameras, incidents, onChange, onClose }: Props) {
  const navigate = useNavigate()
  const root = useRef<HTMLDivElement>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const index = cameras.findIndex(c => c.id === camera.id)
  const mine = incidents.filter(i => i.camera_id === camera.id).slice(0, 12)
  const alarm = mine.find(i => i.status === 'open' && i.severity === 'critical')
  const glow = alarm ? incidentRgb(alarm.type, alarm.severity) : null

  const step = useCallback((by: number) => {
    if (cameras.length < 2) return
    onChange(cameras[(index + by + cameras.length) % cameras.length].id)
  }, [cameras, index, onChange])

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void root.current?.requestFullscreen?.()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'Escape' && !document.fullscreenElement) onClose()
      else if (e.key === 'ArrowRight') step(1)
      else if (e.key === 'ArrowLeft') step(-1)
      else if (e.key === 'f' || e.key === 'F') toggleFullscreen()
    }
    const onFs = () => setFullscreen(Boolean(document.fullscreenElement))
    window.addEventListener('keydown', onKey)
    document.addEventListener('fullscreenchange', onFs)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('fullscreenchange', onFs)
    }
  }, [onClose, step, toggleFullscreen])

  return (
    <motion.div
      ref={root}
      className="fixed inset-0 z-[60] flex flex-col bg-[#050506]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      role="dialog"
      aria-modal="true"
      aria-label={`${camera.name}, full screen`}
    >
      {/* header */}
      <header className="flex items-center gap-3 border-b border-white/[0.08] px-4 py-3 sm:px-6">
        <span className="font-mono text-xs text-[var(--text-muted)]">
          {camera.number ? `#${camera.number}` : ''}
        </span>
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: camera.online ? '#10b981' : '#6b7280' }} />
        <h2 className="min-w-0 truncate text-base font-semibold text-white sm:text-lg">{camera.name}</h2>
        <span
          className="hidden items-center gap-1 rounded-[6px] border px-2 py-0.5 font-mono text-[10px] sm:inline-flex"
          style={{
            color: camera.online ? '#10b981' : '#6b7280',
            borderColor: camera.online ? 'rgba(16,185,129,0.25)' : 'rgba(107,114,128,0.25)',
            background: camera.online ? 'rgba(16,185,129,0.1)' : 'rgba(107,114,128,0.1)',
          }}
        >
          <Activity className="h-3 w-3" />
          {camera.online ? `${camera.fps} FPS` : 'OFFLINE'}
        </span>
        {camera.zones.length > 0 && (
          <span className="hidden items-center gap-1 font-mono text-[11px] text-[var(--text-secondary)] md:inline-flex">
            <Eye className="h-3 w-3" />
            {camera.zones.map(z => z.name).join(', ')}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="hidden font-mono text-[10px] text-[var(--text-muted)] lg:inline">← → camera · F full screen · Esc back</span>
          <IconButton label={fullscreen ? 'Leave full screen' : 'Full screen'} onClick={toggleFullscreen}>
            {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </IconButton>
          <IconButton label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3 sm:p-4 lg:flex-row">
        {/* the stream */}
        <div
          className="group relative flex min-h-[40vh] flex-1 items-center justify-center overflow-hidden rounded-[20px] border bg-black transition-shadow"
          style={{
            borderColor: glow ? `rgba(${glow},0.8)` : 'rgba(255,255,255,0.08)',
            boxShadow: glow ? `0 0 0 1px rgba(${glow},0.6), 0 0 48px rgba(${glow},0.35)` : undefined,
          }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={camera.id}
              className="flex h-full w-full items-center justify-center"
              initial={{ opacity: 0, scale: 0.985 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.16 }}
            >
              {camera.online ? (
                <CameraStream cameraId={camera.id} initialStreamUrl={camera.stream_url} alt={camera.name}
                              className="h-full w-full" fit="contain" />
              ) : (
                <div className="flex flex-col items-center gap-2 text-[var(--text-muted)]">
                  <Video className="h-10 w-10 opacity-30" />
                  <span className="font-mono text-xs">Camera signal offline</span>
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          {alarm && (
            <div
              className="absolute bottom-3 left-3 flex items-center gap-1.5 rounded-full px-3 py-1 font-mono text-[11px] font-semibold text-white"
              style={{ background: `rgb(${glow})` }}
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />
              {typeLabel(alarm.type).toUpperCase()} · OPEN
            </div>
          )}

          {cameras.length > 1 && (
            <>
              <SideArrow side="left" label="Previous camera" onClick={() => step(-1)} />
              <SideArrow side="right" label="Next camera" onClick={() => step(1)} />
            </>
          )}
        </div>

        {/* side panel */}
        <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-[300px]">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => navigate(`/recordings?camera=${encodeURIComponent(camera.id)}`)}
              className="flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-[12px] border border-white/10 px-3 py-2 text-xs text-white hover:bg-white/5"
            >
              <Film className="h-3.5 w-3.5" /> Recordings
            </button>
            <button
              type="button"
              onClick={() => navigate('/restricted')}
              className="flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-[12px] border border-white/10 px-3 py-2 text-xs text-white hover:bg-white/5"
            >
              <Lock className="h-3.5 w-3.5" /> Restricted areas
            </button>
          </div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[20px] border border-white/[0.08] bg-[#0b0b0c]">
            <h3 className="border-b border-white/[0.08] px-4 py-3 text-xs font-semibold uppercase tracking-wider text-white">
              This camera · latest
            </h3>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2.5">
              {mine.length === 0 ? (
                <p className="p-4 text-center font-mono text-[11px] text-[var(--text-muted)]">Nothing on this camera yet.</p>
              ) : mine.map(i => {
                const rgb = incidentRgb(i.type, i.severity)
                return (
                  <button
                    key={i.id}
                    type="button"
                    onClick={() => navigate(`/incidents/${i.id}`)}
                    className="flex w-full cursor-pointer items-center gap-2.5 rounded-[12px] border border-transparent px-2.5 py-2 text-left hover:border-white/10 hover:bg-white/[0.03]"
                  >
                    <span className="h-8 w-1 shrink-0 rounded-full" style={{ background: `rgb(${rgb})` }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-white">{typeLabel(i.type)}</span>
                      <span className="block font-mono text-[10px] text-[var(--text-muted)]">
                        #{i.id} · {formatTime(i.started_at)} · {i.status.replace('_', ' ')}
                      </span>
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        </aside>
      </div>
    </motion.div>
  )
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid h-8 w-8 cursor-pointer place-items-center rounded-full border border-white/10 text-white hover:bg-white/10"
    >
      {children}
    </button>
  )
}

function SideArrow({ side, label, onClick }: { side: 'left' | 'right'; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={`absolute top-1/2 grid h-10 w-10 -translate-y-1/2 cursor-pointer place-items-center rounded-full bg-black/60 text-white opacity-0 backdrop-blur transition-opacity hover:bg-black/80 focus-visible:opacity-100 group-hover:opacity-100 ${
        side === 'left' ? 'left-3' : 'right-3'
      }`}
    >
      {side === 'left' ? <ChevronLeft className="h-5 w-5" /> : <ChevronRight className="h-5 w-5" />}
    </button>
  )
}
