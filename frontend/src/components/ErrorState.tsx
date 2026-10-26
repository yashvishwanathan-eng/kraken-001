/**
 * components/ErrorState.tsx
 * Pro dark bento error state with a clean retry button.
 * Card radius 20px, solid near-black (#0b0b0c), 1px border at rgba(255,255,255,0.08).
 */

import { AlertTriangle } from 'lucide-react'

interface Props {
  message?: string
  onRetry?: () => void
}

export function ErrorState({
  message = 'Something went wrong while loading data.',
  onRetry,
}: Props) {
  return (
    <div
      className="flex flex-col items-center justify-center py-16 px-6 text-center rounded-[20px] border"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
      }}
    >
      <div className="w-12 h-12 rounded-full flex items-center justify-center mb-4 bg-[#f43f5e]/10 border border-[#f43f5e]/25">
        <AlertTriangle className="w-5 h-5 text-[#f43f5e]" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-white mb-1.5 tracking-tight">
        Failed to load
      </h2>
      <p className="text-sm text-[var(--text-secondary)] max-w-sm mb-5 leading-relaxed font-normal">
        {message}
      </p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="h-8 px-4 rounded-full text-xs font-semibold bg-white text-[#0b0b0c] hover:bg-white/90 transition-colors cursor-pointer"
        >
          Retry
        </button>
      )}
    </div>
  )
}
