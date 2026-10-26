/**
 * lib/time.ts
 * All time-formatting helpers for RingGuard.
 * API times are UTC ISO-8601 strings.
 * Display times are always shown in IST (Asia/Kolkata, UTC+5:30) with AM/PM strictly in capitals.
 */

const IST = 'Asia/Kolkata'

function uppercaseAmPm(timeStr: string): string {
  return timeStr.replace(/\b(am|pm)\b/gi, m => m.toUpperCase())
}

/**
 * Format a UTC ISO string as "10:42:13 AM" in IST with capitalized AM/PM.
 */
export function formatTime(utcIso: string): string {
  const str = new Date(utcIso).toLocaleTimeString('en-IN', {
    timeZone: IST,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
  return uppercaseAmPm(str)
}

/**
 * Format a UTC ISO string as "24 Oct 2026" in IST.
 */
export function formatDate(utcIso: string): string {
  return new Date(utcIso).toLocaleDateString('en-IN', {
    timeZone: IST,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

/**
 * Format as "10:42:13 AM · 24 Oct 2026" in IST.
 */
export function formatDateTime(utcIso: string): string {
  return `${formatTime(utcIso)} · ${formatDate(utcIso)}`
}

/**
 * Return a relative time string like "2 min ago", "just now", "3 h ago".
 * Relative to the current local time.
 */
export function fromNow(utcIso: string): string {
  const diffMs = Date.now() - new Date(utcIso).getTime()
  const diffSec = Math.floor(diffMs / 1000)

  if (diffSec < 10) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`

  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin} min ago`

  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} h ago`

  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

/**
 * The primary display format used across the app.
 * "10:42:13 AM · 2 min ago" (with capitalized AM/PM)
 */
export function formatIncidentTime(utcIso: string): string {
  return `${formatTime(utcIso)} · ${fromNow(utcIso)}`
}

/**
 * Current time in IST as a formatted string "10:42:13 AM" with AM/PM in capitals.
 */
export function nowIST(): string {
  const str = new Date().toLocaleTimeString('en-IN', {
    timeZone: IST,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
  return uppercaseAmPm(str)
}

/**
 * Format seconds as "1 min 42 sec" or "42 sec".
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m} min ${s}s` : `${m} min`
}

/**
 * Convert a UTC ISO string to a local Date for input[type=datetime-local].
 */
export function toInputValue(utcIso: string): string {
  const d = new Date(utcIso)
  // Offset for IST
  const offset = 5.5 * 60 * 60 * 1000
  const ist = new Date(d.getTime() + offset)
  return ist.toISOString().slice(0, 16)
}
