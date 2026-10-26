/**
 * SharedLayoutBg: a list whose items share one soft hover highlight that glides from item to item.
 * Written for RingGuard to replace the beui.dev helper that was not supplied. The active item draws its own
 * pill (layoutId in AnimatedSidebarMenuButton); this adds the hover pill underneath.
 */
import { AnimatePresence, motion } from 'framer-motion'
import { forwardRef, useState, type HTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { SPRING_LAYOUT } from './ease'

interface SharedLayoutBgProps extends HTMLAttributes<HTMLElement> {
  as?: 'ul' | 'div'
  inset?: number
  pillClassName?: string
  pillContainerClassName?: string
  children?: ReactNode
}

export const SharedLayoutBg = forwardRef<HTMLElement, SharedLayoutBgProps>(function SharedLayoutBg(
  { as = 'div', inset = 0, pillClassName, pillContainerClassName, className, children, onPointerLeave, ...props },
  ref,
) {
  const [hover, setHover] = useState<{ top: number; height: number } | null>(null)
  const Tag = as as 'div'

  return (
    <Tag
      {...(props as HTMLAttributes<HTMLDivElement>)}
      ref={ref as React.Ref<HTMLDivElement>}
      className={cn('relative', className)}
      onPointerOver={event => {
        const item = (event.target as HTMLElement).closest('[data-slot="sidebar-menu-item"]') as HTMLElement | null
        const list = event.currentTarget as HTMLElement
        if (!item || !list.contains(item)) return
        const button = (item.querySelector('a,button') as HTMLElement | null) ?? item
        setHover({ top: button.offsetTop + inset, height: button.offsetHeight - inset * 2 })
      }}
      onPointerLeave={event => {
        setHover(null)
        onPointerLeave?.(event as React.PointerEvent<HTMLDivElement>)
      }}
    >
      <AnimatePresence>
        {hover && (
          <motion.span
            aria-hidden="true"
            className={cn('pointer-events-none absolute inset-x-0', pillContainerClassName)}
            initial={{ opacity: 0, top: hover.top, height: hover.height }}
            animate={{ opacity: 1, top: hover.top, height: hover.height }}
            exit={{ opacity: 0 }}
            transition={SPRING_LAYOUT}
          >
            <span className={cn('block h-full w-full', pillClassName)} />
          </motion.span>
        )}
      </AnimatePresence>
      {children}
    </Tag>
  )
})
