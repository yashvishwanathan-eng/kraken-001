/**
 * lib/utils.ts
 * Tailwind class merging helper used by shadcn/ui components.
 */
import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
