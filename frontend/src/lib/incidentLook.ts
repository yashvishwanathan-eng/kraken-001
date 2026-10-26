/**
 * lib/incidentLook.ts
 * One place for how an incident looks in the app:
 *   fire  -> orange everywhere (badge, type, alarm glow, banner)
 *   smoke -> grey
 *   every other emergency -> its severity colour (critical red, high amber, low blue)
 */
import type { IncidentType, Severity } from './types'

export const TYPE_LABEL: Record<string, string> = {
  fire: 'Fire', smoke: 'Smoke', no_helmet: 'No helmet (rider)', fall: 'Person down', access: 'Restricted entry',
  unattended: 'Unattended bag', crowding: 'Crowding', ppe: 'Missing helmet or vest', loitering: 'Loitering',
}

const SEVERITY_RGB: Record<Severity, string> = { critical: '244,63,94', high: '245,158,11', low: '59,130,246' }
const TYPE_RGB: Partial<Record<IncidentType, string>> = { fire: '249,115,22', smoke: '156,163,175' }

/** "r,g,b" for this incident: use as rgb(...) or rgba(..., alpha). */
export function incidentRgb(type: string, severity: Severity): string {
  return TYPE_RGB[type as IncidentType] ?? SEVERITY_RGB[severity] ?? SEVERITY_RGB.low
}

export const incidentColor = (type: string, severity: Severity) => `rgb(${incidentRgb(type, severity)})`
export const typeLabel = (type: string) => TYPE_LABEL[type] ?? type.replace(/_/g, ' ')
