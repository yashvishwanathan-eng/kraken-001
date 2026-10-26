/**
 * components/Skeletons.tsx
 * Pro dark bento skeleton loading states.
 * Card radius 20px, solid near-black (#0b0b0c), 1px border at rgba(255,255,255,0.08).
 */

/* Single skeleton block */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

/* Skeleton for an incident table row */
export function SkeletonRow() {
  return (
    <tr className="border-b border-white/[0.06]">
      <td className="px-4 py-3.5"><Skeleton className="h-5 w-20 rounded-[6px]" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-5 w-24 rounded-[6px]" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-4 w-36 rounded" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-4 w-24 rounded" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-4 w-40 rounded" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-5 w-24 rounded-[6px]" /></td>
      <td className="px-4 py-3.5"><Skeleton className="h-4 w-20 rounded" /></td>
    </tr>
  )
}

/* Skeleton for a camera tile */
export function SkeletonCameraTile() {
  return (
    <div
      className="rounded-[20px] border overflow-hidden flex flex-col"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
      }}
    >
      <div className="px-4 py-3 border-b border-white/[0.06] flex items-center justify-between">
        <Skeleton className="h-4 w-32 rounded" />
        <Skeleton className="h-4 w-12 rounded" />
      </div>
      <Skeleton className="aspect-video w-full rounded-none" />
    </div>
  )
}

/* Skeleton for an incident card in the live feed */
export function SkeletonIncidentCard() {
  return (
    <div
      className="p-4 rounded-[20px] border space-y-3"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <Skeleton className="h-5 w-16 rounded-[6px]" />
        <Skeleton className="h-5 w-20 rounded-[6px]" />
      </div>
      <Skeleton className="h-4 w-44 rounded" />
      <div className="pt-2 border-t border-white/[0.06] flex items-center justify-between">
        <Skeleton className="h-3 w-32 rounded" />
        <Skeleton className="h-3 w-4 rounded" />
      </div>
    </div>
  )
}

/* List of N skeleton rows */
export function SkeletonTable({ rows = 8 }: { rows?: number }) {
  return (
    <tbody className="divide-y divide-white/[0.06]">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </tbody>
  )
}

/* List of N skeleton camera tiles */
export function SkeletonCameraGrid({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCameraTile key={i} />
      ))}
    </div>
  )
}
