/**
 * pages/Incidents.tsx
 * Comprehensive safety incidents management page.
 * Features:
 * - Table of incidents from GET /events (newest first).
 * - Exact 7 columns: Severity, Type, Camera Name, Zone, Started Time in IST ("10:42:13 AM · 2 min ago"), Status (StatusChip), Acknowledged By.
 * - Filters above table: type, camera, severity, status, and date range (from/to). Query params match CONTRACT.md exactly and persist in URL.
 * - Search box filtering by camera name, zone, type, or acknowledged-by name. "/" focuses it.
 * - Clicking a row navigates to /incidents/{id}.
 * - Real-time WebSocket updates without page refresh.
 * - Loading skeletons, Empty state ("No incidents match these filters" with Clear filters button), Error state with Retry.
 * - Fits 1366x768 and 1920x1080 without sideways scrolling.
 * - Pro Dark Bento design system: card radius 20px, #0b0b0c background, 1px border at rgba(255,255,255,0.08).
 */

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, X, RotateCcw } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusChip } from '@/components/StatusChip'
import { SkeletonTable } from '@/components/Skeletons'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { fetchEvents, fetchCameras } from '@/lib/api'
import { wsClient } from '@/lib/ws'
import { formatIncidentTime } from '@/lib/time'
import type { Incident, IncidentType, Severity, IncidentStatus, WsEvent, Camera } from '@/lib/types'

const INCIDENT_TYPES: { value: IncidentType; label: string }[] = [
  { value: 'access', label: 'Access Violation' },
  { value: 'ppe', label: 'PPE Violation' },
  { value: 'fall', label: 'Person Fall' },
  { value: 'fire', label: 'Fire / Smoke' },
  { value: 'unattended', label: 'Unattended Object' },
  { value: 'loitering', label: 'Loitering' },
  { value: 'crowding', label: 'Crowding' },
]

const SEVERITIES: { value: Severity; label: string }[] = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'low', label: 'Low' },
]

const STATUSES: { value: IncidentStatus; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'false_alarm', label: 'False Alarm' },
]

export function IncidentsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const searchInputRef = useRef<HTMLInputElement>(null)

  // URL Query Parameters
  const typeParam     = (searchParams.get('type') as IncidentType) || ''
  const cameraParam   = searchParams.get('camera') || ''
  const severityParam = (searchParams.get('severity') as Severity) || ''
  const statusParam   = (searchParams.get('status') as IncidentStatus) || ''
  const fromParam     = searchParams.get('from') || ''
  const toParam       = searchParams.get('to') || ''
  const queryParam    = searchParams.get('q') || ''

  // Tick for updating relative IST times ("2 min ago") every 30s
  const [, setTick] = useState(0)
  useEffect(() => {
    const interval = setInterval(() => setTick(t => t + 1), 30_000)
    return () => clearInterval(interval)
  }, [])

  // Keyboard shortcut: "/" focuses search box
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === '/') {
        const target = e.target as HTMLElement | null
        const isInputField =
          target?.tagName === 'INPUT' ||
          target?.tagName === 'TEXTAREA' ||
          target?.tagName === 'SELECT' ||
          target?.isContentEditable

        if (!isInputField) {
          e.preventDefault()
          searchInputRef.current?.focus()
          searchInputRef.current?.select()
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Helper to update search params in the URL
  const updateFilter = useCallback(
    (key: string, value: string) => {
      setSearchParams(
        prev => {
          const next = new URLSearchParams(prev)
          if (value) {
            next.set(key, value)
          } else {
            next.delete(key)
          }
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  const clearAllFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true })
  }, [setSearchParams])

  const hasActiveFilters = Boolean(
    typeParam || cameraParam || severityParam || statusParam || fromParam || toParam || queryParam,
  )

  // Fetch camera list to populate the camera filter dropdown
  const { data: cameras } = useQuery<Camera[]>({
    queryKey: ['cameras'],
    queryFn: fetchCameras,
    staleTime: 60_000,
  })

  // Build filters for GET /events exactly matching CONTRACT.md
  const apiFilters = useMemo(() => {
    return {
      type: typeParam || undefined,
      camera: cameraParam || undefined,
      severity: severityParam || undefined,
      status: statusParam || undefined,
      from: fromParam ? (fromParam.endsWith('Z') ? fromParam : new Date(fromParam).toISOString()) : undefined,
      to: toParam ? (toParam.endsWith('Z') ? toParam : new Date(toParam).toISOString()) : undefined,
    }
  }, [typeParam, cameraParam, severityParam, statusParam, fromParam, toParam])

  // Fetch incidents
  const {
    data: fetchedIncidents,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<Incident[]>({
    queryKey: ['events', apiFilters],
    queryFn: () => fetchEvents(apiFilters),
  })

  // Local incidents state to support real-time WebSocket prepending/updates
  const [incidents, setIncidents] = useState<Incident[]>([])

  useEffect(() => {
    if (fetchedIncidents) {
      setIncidents(
        [...fetchedIncidents].sort(
          (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
        ),
      )
    }
  }, [fetchedIncidents])

  // Handle incoming real-time incident via WebSocket
  const handleIncomingIncident = useCallback(
    (incoming: Incident) => {
      setIncidents(prev => {
        const exists = prev.some(i => i.id === incoming.id)
        if (exists) {
          // Update in place
          return prev.map(i => (i.id === incoming.id ? incoming : i))
        }

        // New incident: check if it satisfies active server filters
        if (typeParam && incoming.type !== typeParam) return prev
        if (cameraParam && incoming.camera_id !== cameraParam) return prev
        if (severityParam && incoming.severity !== severityParam) return prev
        if (statusParam && incoming.status !== statusParam) return prev
        if (fromParam && new Date(incoming.started_at) < new Date(fromParam)) return prev
        if (toParam && new Date(incoming.started_at) > new Date(toParam)) return prev

        // Prepend newest first
        return [incoming, ...prev]
      })
    },
    [typeParam, cameraParam, severityParam, statusParam, fromParam, toParam],
  )

  // Subscribe to WebSocket events
  useEffect(() => {
    const unsubscribe = wsClient.subscribe((event: WsEvent) => {
      let inc: Incident | null = null
      if (event && typeof event === 'object') {
        if ('id' in event && 'type' in event && 'severity' in event) {
          inc = event as Incident
        } else if (
          'data' in event &&
          event.data &&
          typeof event.data === 'object' &&
          'id' in event.data
        ) {
          inc = event.data as Incident
        }
      }
      if (inc) {
        handleIncomingIncident(inc)
      }
    })
    return unsubscribe
  }, [handleIncomingIncident])

  // Client-side search filtering across camera name, zone, type, and acknowledged-by
  const filteredIncidents = useMemo(() => {
    if (!queryParam.trim()) return incidents
    const q = queryParam.trim().toLowerCase()
    return incidents.filter(i => {
      const matchCam   = i.camera_name.toLowerCase().includes(q)
      const matchZone  = i.zone.toLowerCase().includes(q)
      const matchType  = i.type.toLowerCase().includes(q)
      const matchAckBy = (i.acknowledged_by || '').toLowerCase().includes(q)
      return matchCam || matchZone || matchType || matchAckBy
    })
  }, [incidents, queryParam])

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <PageHeader
        title="Incidents"
        description="Search, filter, and review detected workplace safety incidents"
        actions={
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-[var(--text-secondary)]">
              {filteredIncidents.length} {filteredIncidents.length === 1 ? 'incident' : 'incidents'}
            </span>
          </div>
        }
      />

      {/* ── Filter Bar ─────────────────────────────────────────── */}
      <div
        className="rounded-[20px] border p-4 space-y-3"
        style={{
          background: '#0b0b0c',
          borderColor: 'rgba(255, 255, 255, 0.08)',
        }}
      >
        <div className="flex flex-col md:flex-row gap-3 items-stretch md:items-center justify-between">
          {/* Search Box */}
          <div className="relative flex-1 min-w-[240px]">
            <Search className="w-4 h-4 text-[var(--text-muted)] absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              ref={searchInputRef}
              type="text"
              value={queryParam}
              onChange={e => updateFilter('q', e.target.value)}
              placeholder="Search camera, zone, type or responder... (press '/' to focus)"
              className="w-full h-9 pl-9 pr-8 text-xs bg-black text-white rounded-full border border-white/10 placeholder:text-[var(--text-muted)] focus:outline-none focus:border-white/30 transition-colors"
            />
            {queryParam && (
              <button
                type="button"
                onClick={() => updateFilter('q', '')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-white"
                aria-label="Clear search"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Quick Clear Button */}
          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearAllFilters}
              className="inline-flex items-center gap-1.5 h-9 px-3 rounded-full text-xs font-mono font-medium text-[var(--text-secondary)] hover:text-white bg-white/5 border border-white/10 hover:bg-white/10 transition-colors cursor-pointer shrink-0 self-start md:self-auto"
            >
              <RotateCcw className="w-3 h-3" />
              <span>Clear filters</span>
            </button>
          )}
        </div>

        {/* Dropdowns & Date Range */}
        <div className="flex flex-wrap items-center gap-2.5 pt-2 border-t border-white/[0.06]">
          {/* Type Filter */}
          <select
            value={typeParam}
            onChange={e => updateFilter('type', e.target.value)}
            className="h-8 px-3 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer"
            aria-label="Filter by incident type"
          >
            <option value="">All Types</option>
            {INCIDENT_TYPES.map(t => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>

          {/* Camera Filter */}
          <select
            value={cameraParam}
            onChange={e => updateFilter('camera', e.target.value)}
            className="h-8 px-3 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer max-w-[180px] truncate"
            aria-label="Filter by camera"
          >
            <option value="">All Cameras</option>
            {(cameras || []).map(cam => (
              <option key={cam.id} value={cam.id}>
                {cam.name}
              </option>
            ))}
          </select>

          {/* Severity Filter */}
          <select
            value={severityParam}
            onChange={e => updateFilter('severity', e.target.value)}
            className="h-8 px-3 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer"
            aria-label="Filter by severity"
          >
            <option value="">All Severities</option>
            {SEVERITIES.map(s => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>

          {/* Status Filter */}
          <select
            value={statusParam}
            onChange={e => updateFilter('status', e.target.value)}
            className="h-8 px-3 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer"
            aria-label="Filter by status"
          >
            <option value="">All Statuses</option>
            {STATUSES.map(s => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>

          {/* Date Range: From */}
          <div className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] font-mono">
            <span>From</span>
            <input
              type="date"
              value={fromParam ? fromParam.slice(0, 10) : ''}
              onChange={e => {
                const val = e.target.value ? `${e.target.value}T00:00:00Z` : ''
                updateFilter('from', val)
              }}
              className="h-8 px-2.5 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer"
              aria-label="Filter from date"
            />
          </div>

          {/* Date Range: To */}
          <div className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] font-mono">
            <span>To</span>
            <input
              type="date"
              value={toParam ? toParam.slice(0, 10) : ''}
              onChange={e => {
                const val = e.target.value ? `${e.target.value}T23:59:59Z` : ''
                updateFilter('to', val)
              }}
              className="h-8 px-2.5 rounded-full text-xs font-mono bg-black text-white border border-white/10 focus:outline-none focus:border-white/30 cursor-pointer"
              aria-label="Filter to date"
            />
          </div>
        </div>
      </div>

      {/* ── Incidents Table Card ───────────────────────────────── */}
      <div
        className="rounded-[20px] border overflow-hidden"
        style={{
          background: '#0b0b0c',
          borderColor: 'rgba(255, 255, 255, 0.08)',
        }}
      >
        {isError ? (
          <div className="p-8">
            <ErrorState
              message={error instanceof Error ? error.message : 'Failed to fetch incidents.'}
              onRetry={() => refetch()}
            />
          </div>
        ) : isLoading ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-white/[0.08] text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                  <th className="px-4 py-3.5">Severity</th>
                  <th className="px-4 py-3.5">Type</th>
                  <th className="px-4 py-3.5">Camera Name</th>
                  <th className="px-4 py-3.5">Zone</th>
                  <th className="px-4 py-3.5">Started Time (IST)</th>
                  <th className="px-4 py-3.5">Status</th>
                  <th className="px-4 py-3.5">Acknowledged By</th>
                </tr>
              </thead>
              <SkeletonTable rows={8} />
            </table>
          </div>
        ) : filteredIncidents.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title="No incidents match these filters"
              description="Try adjusting your search terms or date range, or reset your filters."
              action={
                hasActiveFilters ? (
                  <button
                    type="button"
                    onClick={clearAllFilters}
                    className="h-8 px-4 rounded-full text-xs font-semibold bg-white text-[#0b0b0c] hover:bg-white/90 transition-colors cursor-pointer"
                  >
                    Clear filters
                  </button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-white/[0.08] text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)] bg-white/[0.02]">
                  <th className="px-4 py-3.5 w-[110px]">Severity</th>
                  <th className="px-4 py-3.5 w-[140px]">Type</th>
                  <th className="px-4 py-3.5 min-w-[180px]">Camera Name</th>
                  <th className="px-4 py-3.5 w-[130px]">Zone</th>
                  <th className="px-4 py-3.5 w-[220px]">Started Time (IST)</th>
                  <th className="px-4 py-3.5 w-[130px]">Status</th>
                  <th className="px-4 py-3.5 w-[140px]">Acknowledged By</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {filteredIncidents.map(incident => (
                  <tr
                    key={incident.id}
                    onClick={() => navigate(`/incidents/${incident.id}`)}
                    className="hover:bg-white/[0.03] transition-colors cursor-pointer select-none group"
                    tabIndex={0}
                    role="button"
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/incidents/${incident.id}`)
                      }
                    }}
                  >
                    {/* Severity */}
                    <td className="px-4 py-3.5">
                      <SeverityBadge severity={incident.severity} size="sm" />
                    </td>

                    {/* Type */}
                    <td className="px-4 py-3.5">
                      <span className="font-mono text-[11px] uppercase tracking-wider text-white/90">
                        {incident.type}
                      </span>
                    </td>

                    {/* Camera Name */}
                    <td className="px-4 py-3.5">
                      <div className="font-medium text-white truncate max-w-[240px]">
                        {incident.camera_name}
                      </div>
                    </td>

                    {/* Zone */}
                    <td className="px-4 py-3.5">
                      <span className="font-mono text-[11px] text-[var(--text-secondary)] px-2 py-0.5 rounded-[6px] bg-white/5 border border-white/5">
                        {incident.zone}
                      </span>
                    </td>

                    {/* Started Time (IST) */}
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      <span className="font-mono text-[11.5px] text-[var(--text-secondary)]">
                        {formatIncidentTime(incident.started_at)}
                      </span>
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3.5">
                      <StatusChip status={incident.status} size="sm" />
                    </td>

                    {/* Acknowledged By */}
                    <td className="px-4 py-3.5">
                      <span className="font-mono text-[11.5px] text-[var(--text-secondary)]">
                        {incident.acknowledged_by || '—'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
