/**
 * components/recordings/RecordingPlayer.tsx
 * Plays one 5-minute recording file and jumps to a second inside it (e.g. 10 s before an incident).
 * When the file ends it can move on to the next one, so a long stretch plays without clicking.
 */
import { useEffect, useRef } from 'react'
import { mediaUrl } from '@/lib/admin'

export function RecordingPlayer({ url, startAt = 0, onEnded, autoPlay = true }: {
  url: string
  startAt?: number
  onEnded?: () => void
  autoPlay?: boolean
}) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const video = ref.current
    if (!video) return
    const seek = () => {
      if (startAt > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(startAt, Math.max(0, video.duration - 0.5))
      if (autoPlay) void video.play().catch(() => undefined)
    }
    video.addEventListener('loadedmetadata', seek, { once: true })
    return () => video.removeEventListener('loadedmetadata', seek)
  }, [url, startAt, autoPlay])
  return (
    <video
      key={url}
      ref={ref}
      src={mediaUrl(url)}
      controls
      muted
      playsInline
      onEnded={onEnded}
      className="aspect-video w-full rounded-[14px] bg-black"
    />
  )
}
