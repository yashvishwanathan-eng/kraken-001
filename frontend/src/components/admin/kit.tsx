/**
 * components/admin/kit.tsx
 * Small building blocks for the admin screens, in the bento style (26 px tiles, mono numbers):
 * Tile, Button, Field, Select, Modal, Tabs, Dot, useMe (the logged-in user's actions) and useCountUp.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { fetchMe } from '@/lib/api'
import { cn } from '@/lib/utils'

export function useMe() {
  const { data } = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000 })
  const can = (action: string) => Boolean(data?.actions?.includes(action))
  return { me: data, can }
}

export function useCountUp(target: number, duration = 1100) {
  const [value, setValue] = useState(0)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setValue(target)
      return
    }
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      setValue(target * (1 - Math.pow(1 - p, 4)))
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return value
}

export function Tile({ title, action, className, style, children }: {
  title?: ReactNode
  action?: ReactNode
  className?: string
  style?: React.CSSProperties
  children: ReactNode
}) {
  return (
    <section
      className={cn('relative flex min-w-0 flex-col overflow-hidden rounded-[26px] border border-border bg-card p-5 text-card-foreground', className)}
      style={style}
    >
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h3 className="text-[13px] font-medium text-muted-foreground">{title}</h3>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Button({ variant = 'ghost', className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
}) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-[12px] px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed',
        variant === 'primary' && 'bg-white text-black hover:bg-white/90',
        variant === 'ghost' && 'border border-border text-white hover:bg-white/5',
        variant === 'danger' && 'border border-[rgba(244,63,94,0.4)] text-[var(--critical)] hover:bg-[var(--critical-subtle)]',
        className,
      )}
    />
  )
}

export function Field({ label, hint, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-muted-foreground">{label}</span>
      <input
        {...props}
        className="w-full rounded-[10px] border border-border bg-black px-2.5 py-1.5 text-sm text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
      />
      {hint && <span className="mt-1 block text-[10px] text-[var(--text-muted)]">{hint}</span>}
    </label>
  )
}

export function Select({ label, options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
  options: { value: string; label: string }[]
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-muted-foreground">{label}</span>
      <select
        {...props}
        className="w-full rounded-[10px] border border-border bg-black px-2.5 py-1.5 text-sm text-white focus:border-white/40 focus:outline-none"
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  )
}

export function Checks({ label, options, value, onChange }: {
  label: string
  options: { value: string; label: string }[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  return (
    <fieldset>
      <legend className="mb-1 text-[11px] text-muted-foreground">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map(o => {
          const on = value.includes(o.value)
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(on ? value.filter(v => v !== o.value) : [...value, o.value])}
              className={cn(
                'rounded-full border px-2.5 py-1 text-[11px] font-mono transition-colors cursor-pointer',
                on ? 'border-white bg-white text-black' : 'border-border text-muted-foreground hover:text-white',
              )}
            >
              {o.label}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={e => e.stopPropagation()}
        className={cn('max-h-[90vh] w-full overflow-y-auto rounded-[22px] border border-border bg-[var(--surface-raised)] p-5', wide ? 'max-w-2xl' : 'max-w-md')}
      >
        <header className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1 text-muted-foreground hover:bg-white/5 hover:text-white cursor-pointer">
            <X className="h-4 w-4" />
          </button>
        </header>
        {children}
      </div>
    </div>
  )
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="inline-flex rounded-full border border-border p-0.5">
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-full px-3 py-1 text-xs font-medium transition-colors cursor-pointer',
            o.value === value ? 'bg-white text-black' : 'text-muted-foreground hover:text-white',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Dot({ on, className }: { on: boolean; className?: string }) {
  return <span className={cn('inline-block size-2 shrink-0 rounded-full', className)} style={{ background: on ? 'var(--online)' : 'var(--neutral)' }} aria-hidden="true" />
}

export function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null
  return <p className="text-xs" style={{ color: 'var(--critical)' }}>{error instanceof Error ? error.message : String(error)}</p>
}

export const SEVERITY_COLOR: Record<string, string> = { critical: 'var(--critical)', high: 'var(--high)', low: 'var(--low)' }

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000)
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/** Times are always shown in IST, like the clock in the top bar, whatever the computer's own time zone. */
export const TZ = 'Asia/Kolkata'
export const istHour = () => Number(new Date().toLocaleString('en-GB', { timeZone: TZ, hour: '2-digit', hour12: false }))
