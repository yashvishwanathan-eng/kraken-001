/**
 * Motion curves for the animated sidebar (written for RingGuard: the original beui.dev helper was not supplied).
 * Cubic-bezier arrays and spring settings in framer-motion's format.
 */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const
export const EASE_DRAWER = [0.32, 0.72, 0, 1] as const

/** Layout moves (the active pill sliding between items, list items shifting). */
export const SPRING_LAYOUT = { type: 'spring', stiffness: 520, damping: 42, mass: 0.7 } as const

/** Press feedback on buttons. */
export const SPRING_PRESS = { type: 'spring', stiffness: 700, damping: 35 } as const
