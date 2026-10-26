/**
 * components/EmptyState.tsx
 * Pro dark bento empty state.
 * Card radius 20px, solid near-black (#0b0b0c), 1px border at rgba(255,255,255,0.08).
 */

import { type LucideIcon, Inbox } from 'lucide-react'

interface Props {
  icon?: LucideIcon
  title: string
  description: string
  action?: React.ReactNode
}

export function EmptyState({ icon: Icon = Inbox, title, description, action }: Props) {
  return (
    <div
      className="flex flex-col items-center justify-center py-16 px-6 text-center rounded-[20px] border"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
      }}
    >
      <div className="w-12 h-12 rounded-full flex items-center justify-center mb-4 bg-white/5 border border-white/10">
        <Icon className="w-5 h-5 text-[var(--text-secondary)]" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-white mb-1.5 tracking-tight">{title}</h2>
      <p className="text-sm text-[var(--text-secondary)] max-w-sm mb-4 leading-relaxed font-normal">
        {description}
      </p>
      {action && <div>{action}</div>}
    </div>
  )
}
