/**
 * components/PageHeader.tsx
 * Pro top-of-page header.
 * Page titles are 28px, weight 600, letter-spacing -0.02em. No emoji or decorative icons.
 */

interface Props {
  title: string
  description?: string
  actions?: React.ReactNode
}

export function PageHeader({ title, description, actions }: Props) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-[28px] font-semibold tracking-[-0.02em] text-white leading-tight">
          {title}
        </h1>
        {description && (
          <p className="mt-1 text-sm text-[var(--text-secondary)] font-normal">{description}</p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-2 shrink-0">{actions}</div>
      )}
    </div>
  )
}
