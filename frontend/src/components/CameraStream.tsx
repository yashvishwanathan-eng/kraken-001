/**
 * components/CameraStream.tsx
 * Resilient MJPEG camera stream component.
 * In mock mode (VITE_USE_MOCKS=true): shows "Stream unavailable in mock mode" without the Refresh button.
 * In real mode: loads stream and automatically fetches a fresh signed stream URL on error with Refresh button.
 * Solid near-black, 1px border at rgba(255,255,255,0.08).
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { VideoOff, RefreshCw } from 'lucide-react'
import { fetchCameraStreamUrl, resolveMediaUrl } from '@/lib/api'

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

interface CameraStreamProps {
  cameraId: string
  initialStreamUrl?: string
  alt?: string
  className?: string
  onLoad?: () => void
}

export function CameraStream({
  cameraId,
  initialStreamUrl,
  alt = 'Live camera stream',
  className = '',
  onLoad,
}: CameraStreamProps) {
  const [streamUrl, setStreamUrl] = useState<string>(() =>
    resolveMediaUrl(initialStreamUrl || `/stream/${cameraId}`),
  )
  const [hasError, setHasError] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const retryCount = useRef(0)

  // Update streamUrl if initialStreamUrl changes
  useEffect(() => {
    if (initialStreamUrl) {
      setStreamUrl(resolveMediaUrl(initialStreamUrl))
      setHasError(false)
      retryCount.current = 0
    }
  }, [initialStreamUrl])

  const refreshStream = useCallback(async () => {
    setIsRefreshing(true)
    try {
      const freshUrl = await fetchCameraStreamUrl(cameraId)
      // Append cache buster to force img reload
      const sep = freshUrl.includes('?') ? '&' : '?'
      setStreamUrl(`${freshUrl}${sep}_t=${Date.now()}`)
      setHasError(false)
    } catch {
      setHasError(true)
    } finally {
      setIsRefreshing(false)
    }
  }, [cameraId])

  const handleError = async () => {
    // If first failure, automatically attempt to fetch fresh signed stream URL
    if (retryCount.current < 2) {
      retryCount.current += 1
      await refreshStream()
    } else {
      setHasError(true)
    }
  }

  const handleImageLoaded = () => {
    setHasError(false)
    retryCount.current = 0
    onLoad?.()
  }

  // In mock mode, tiles show clean "Stream unavailable in mock mode" without refresh button
  if (USE_MOCKS) {
    return (
      <div
        className={`flex flex-col items-center justify-center p-4 text-center ${className}`}
        style={{
          background: '#0b0b0c',
          minHeight: '200px',
        }}
      >
        <VideoOff className="w-7 h-7 text-[var(--text-muted)] mb-2 opacity-40" />
        <span className="text-xs font-mono text-[var(--text-secondary)]">
          Stream unavailable in mock mode
        </span>
      </div>
    )
  }

  // Real backend error state: keep Refresh behaviour with pro bento styling
  if (hasError) {
    return (
      <div
        className={`flex flex-col items-center justify-center p-4 text-center ${className}`}
        style={{
          background: '#0b0b0c',
          minHeight: '200px',
        }}
      >
        <VideoOff className="w-7 h-7 text-[var(--text-muted)] mb-2" />
        <span className="text-xs font-mono text-[var(--text-secondary)] mb-3">
          Stream unavailable or expired
        </span>
        <button
          type="button"
          onClick={() => {
            retryCount.current = 0
            refreshStream()
          }}
          disabled={isRefreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-[#0b0b0c] bg-white hover:bg-white/90 transition-colors disabled:opacity-50 cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          <span>{isRefreshing ? 'Reconnecting...' : 'Refresh Stream'}</span>
        </button>
      </div>
    )
  }

  return (
    <div className={`relative overflow-hidden ${className}`}>
      <img
        src={streamUrl}
        alt={alt}
        onError={handleError}
        onLoad={handleImageLoaded}
        className="w-full h-full object-cover"
        loading="lazy"
      />
    </div>
  )
}
