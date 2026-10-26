/**
 * app/Layout.tsx
 * Pro dark bento app shell:
 * - Solid #000000 page with solid #0b0b0c surfaces and 1px borders at rgba(255,255,255,0.08).
 * - Monospace Geist Mono time and metrics.
 * - Meaningful status indicators only.
 */

import { useState, useEffect } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutGrid,
  AlertTriangle,
  MessageSquare,
  Users,
  MapPin,
  BarChart3,
  Shield,
  Video,
  ChevronLeft,
  ChevronRight,
  Database,
  Phone,
  Brain,
  LogOut,
  User as UserIcon,
  X,
} from 'lucide-react'
import { APP_NAME } from '@/lib/constants'
import { fetchHealth } from '@/lib/api'
import { nowIST } from '@/lib/time'
import { useAuth } from '@/lib/auth'
import { wsClient } from '@/lib/ws'
import type { AlertAllNotice } from '@/lib/types'

const NAV = [
  { to: '/live',       icon: Video,         label: 'Live Feed'   },
  { to: '/incidents',  icon: AlertTriangle, label: 'Incidents'   },
  { to: '/assistant',  icon: MessageSquare, label: 'Assistant'   },
  { to: '/overview',   icon: BarChart3,     label: 'Overview'    },
  { to: '/responders', icon: Users,         label: 'Responders'  },
  { to: '/zones',      icon: MapPin,        label: 'Zones'       },
]

export function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const [time, setTime] = useState(nowIST())
  const [alertNotice, setAlertNotice] = useState<AlertAllNotice | null>(null)
  const { user, logout } = useAuth()

  // Tick the clock every second with guaranteed uppercase AM/PM
  useEffect(() => {
    const id = setInterval(() => setTime(nowIST()), 1000)
    return () => clearInterval(id)
  }, [])

  // Listen for WebSocket alert_all emergency broadcast messages
  useEffect(() => {
    const unsubscribe = wsClient.subscribe(event => {
      if (event && typeof event === 'object' && 'kind' in event && event.kind === 'alert_all') {
        setAlertNotice(event as AlertAllNotice)
      }
    })
    return unsubscribe
  }, [])

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
  })

  const camerasOnline = health?.cameras.filter(c => c.online).length ?? 0
  const camerasTotal  = health?.cameras.length ?? 0
  const dbOnline      = health?.db === 'ok' || health?.db === true
  const twilioOnline  = health?.twilio === 'ok' || health?.twilio === true
  const llmOnline     = health?.llm === 'ok' || health?.llm === 'ollama_only' || health?.llm === true

  return (
    <div className="flex flex-col h-dvh overflow-hidden bg-black text-white">
      {/* ── Emergency alert_all banner across top of app ──── */}
      {alertNotice && (
        <div
          role="alert"
          className="w-full bg-[#f43f5e] text-white px-4 py-2.5 flex items-center justify-between text-xs sm:text-sm font-semibold shadow-lg shrink-0 z-50 border-b border-[#f43f5e]"
        >
          <div className="flex items-center gap-2.5 overflow-hidden">
            <AlertTriangle className="w-4 h-4 shrink-0 text-white" />
            <span className="truncate">
              EMERGENCY BROADCAST ALERT: {alertNotice.message}
            </span>
            {alertNotice.by && (
              <span className="text-xs opacity-90 hidden md:inline font-mono font-normal">
                (Initiated by {alertNotice.by})
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => setAlertNotice(null)}
            className="ml-4 flex items-center gap-1 px-2.5 py-1 rounded-full bg-black/30 hover:bg-black/50 text-white text-xs font-medium transition-colors shrink-0 cursor-pointer"
            aria-label="Dismiss alert"
          >
            <X className="w-3.5 h-3.5" />
            <span>Dismiss</span>
          </button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* ── Sidebar ─────────────────────────────────────────── */}
        <aside
          className="flex flex-col shrink-0 h-full border-r transition-all duration-200"
          style={{
            width: collapsed ? '60px' : '220px',
            background: '#0b0b0c',
            borderColor: 'rgba(255, 255, 255, 0.08)',
          }}
        >
          {/* Logo */}
          <div
            className="flex items-center gap-3 px-4 py-4 border-b"
            style={{ borderColor: 'rgba(255, 255, 255, 0.08)', minHeight: '60px' }}
          >
            <div className="w-8 h-8 rounded-[10px] flex items-center justify-center shrink-0 bg-white/10 border border-white/10">
              <Shield className="w-4 h-4 text-white" aria-hidden="true" />
            </div>
            {!collapsed && (
              <span className="font-semibold text-sm tracking-tight text-white truncate">
                {APP_NAME}
              </span>
            )}
          </div>

          {/* Nav */}
          <nav className="flex-1 py-3 px-2 overflow-hidden space-y-1" aria-label="Main navigation">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2 rounded-[12px] text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-white ${
                    isActive
                      ? 'text-white bg-white/10'
                      : 'text-[var(--text-secondary)] hover:text-white hover:bg-white/5'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon
                      className={`w-4 h-4 shrink-0 ${isActive ? 'text-white' : 'text-[var(--text-muted)]'}`}
                      aria-hidden="true"
                    />
                    {!collapsed && <span className="truncate">{label}</span>}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* Collapse toggle */}
          <div
            className="p-3 border-t"
            style={{ borderColor: 'rgba(255, 255, 255, 0.08)' }}
          >
            <button
              onClick={() => setCollapsed(c => !c)}
              className="flex items-center justify-center w-full py-1.5 rounded-[10px] text-xs text-[var(--text-muted)] hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {collapsed ? (
                <ChevronRight className="w-4 h-4" />
              ) : (
                <div className="flex items-center gap-2">
                  <ChevronLeft className="w-4 h-4" />
                  <span>Collapse</span>
                </div>
              )}
            </button>
          </div>
        </aside>

        {/* ── Main column ─────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0 bg-black">
          {/* Top bar */}
          <header
            className="flex items-center gap-4 px-5 border-b shrink-0"
            style={{
              height: '60px',
              background: '#0b0b0c',
              borderColor: 'rgba(255, 255, 255, 0.08)',
            }}
          >
            {/* Status pills */}
            <div className="flex items-center gap-2.5 flex-1 overflow-hidden">
              {/* Cameras */}
              <StatusPill
                icon={<LayoutGrid className="w-3 h-3" />}
                label={`Cameras ${camerasOnline}/${camerasTotal}`}
                online={camerasOnline === camerasTotal && camerasTotal > 0}
                title={`${camerasOnline} of ${camerasTotal} cameras online`}
              />
              {/* AI / LLM */}
              <StatusPill
                icon={<Brain className="w-3 h-3" />}
                label="AI"
                online={llmOnline}
                title={
                  health?.llm === 'ollama_only'
                    ? 'AI inference online (Ollama)'
                    : llmOnline
                    ? 'AI inference online (Groq)'
                    : 'AI inference offline'
                }
              />
              {/* Database */}
              <StatusPill
                icon={<Database className="w-3 h-3" />}
                label="DB"
                online={dbOnline}
                title={dbOnline ? 'Database online' : 'Database offline'}
              />
              {/* Twilio */}
              <StatusPill
                icon={<Phone className="w-3 h-3" />}
                label="Calls"
                online={twilioOnline}
                title={
                  health?.twilio === 'not_configured'
                    ? 'Twilio not configured'
                    : twilioOnline
                    ? 'Twilio voice/SMS online'
                    : 'Twilio offline'
                }
              />
            </div>

            {/* Right section: Clock, User profile, Logout */}
            <div className="flex items-center gap-4 shrink-0">
              {/* Clock with AM/PM in capitals */}
              <time
                className="text-xs tabular text-[var(--text-secondary)] shrink-0 font-mono hidden md:inline"
                aria-label={`Current time: ${time} IST`}
              >
                {time} <span className="text-[var(--text-muted)] text-[10px]">IST</span>
              </time>

              {/* Logged in User Profile & Role */}
              <div className="flex items-center gap-2.5 pl-3 border-l border-white/10 shrink-0">
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-bold text-white uppercase shrink-0 bg-white/10 border border-white/15"
                  title={user?.name || 'User'}
                >
                  {user?.name ? (
                    user.name.slice(0, 2).toUpperCase()
                  ) : (
                    <UserIcon className="w-3.5 h-3.5" />
                  )}
                </div>
                <div className="flex flex-col text-left">
                  <span className="text-xs font-medium text-white leading-tight max-w-[120px] truncate">
                    {user?.name || 'Admin'}
                  </span>
                  <span className="text-[10px] font-mono text-[var(--text-muted)] leading-none mt-0.5">
                    {user?.role || 'Admin'}
                  </span>
                </div>

                {/* Logout Button */}
                <button
                  type="button"
                  onClick={logout}
                  className="ml-1.5 flex items-center gap-1.5 px-2.5 py-1 rounded-[10px] text-xs font-mono text-[var(--text-secondary)] hover:text-white hover:bg-white/5 border border-white/10 transition-colors cursor-pointer"
                  title="Log out"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Logout</span>
                </button>
              </div>
            </div>
          </header>

          {/* Page content */}
          <main className="flex-1 overflow-y-auto p-5 min-h-0 bg-black" id="main-content">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  )
}

/* ── Status Pill ────────────────────────────────────────── */

interface StatusPillProps {
  icon: React.ReactNode
  label: string
  online: boolean
  title: string
}

function StatusPill({ icon, label, online, title }: StatusPillProps) {
  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono border"
      style={{
        background: '#0b0b0c',
        borderColor: 'rgba(255, 255, 255, 0.08)',
        color: 'var(--text-secondary)',
      }}
      title={title}
    >
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{
          background: online ? '#10b981' : '#6b7280',
        }}
      />
      <span className="text-[var(--text-muted)]">{icon}</span>
      <span className="text-white text-[11px]">{label}</span>
    </div>
  )
}
