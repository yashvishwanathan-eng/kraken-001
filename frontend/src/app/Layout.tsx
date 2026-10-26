/**
 * app/Layout.tsx
 * App shell: the animated sidebar (beui.dev, see components/ui/animated-sidebar.tsx) with every screen in three
 * groups, the emergency banner, the status bar (cameras, AI, DB, calls, Demo alert, clock, user) and the page.
 * Screens a role cannot use are hidden from the sidebar (the API refuses them anyway).
 */

import { useState, useEffect } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutGrid,
  AlertTriangle,
  Sparkles,
  Users,
  MapPin,
  Shield,
  ShieldCheck,
  Video,
  Camera,
  ScrollText,
  Database,
  Phone,
  Brain,
  LogOut,
  PanelLeft,
  User as UserIcon,
  X,
} from 'lucide-react'
import { APP_NAME } from '@/lib/constants'
import { fetchHealth } from '@/lib/api'
import { nowIST } from '@/lib/time'
import { useAuth } from '@/lib/auth'
import { wsClient } from '@/lib/ws'
import { DemoAlert } from '@/components/demo/DemoAlert'
import { useMe } from '@/components/admin/kit'
import {
  AnimatedSidebar,
  AnimatedSidebarContent,
  AnimatedSidebarFooter,
  AnimatedSidebarGroup,
  AnimatedSidebarGroupContent,
  AnimatedSidebarGroupLabel,
  AnimatedSidebarHeader,
  AnimatedSidebarInset,
  AnimatedSidebarMenu,
  AnimatedSidebarMenuButton,
  AnimatedSidebarMenuItem,
  AnimatedSidebarProvider,
  AnimatedSidebarRail,
  AnimatedSidebarTrigger,
} from '@/components/ui/animated-sidebar'
import type { AlertAllNotice } from '@/lib/types'

interface NavItem {
  to: string
  icon: typeof Video
  label: string
  needs?: string[] // shown when the user holds any of these actions
}

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Monitor',
    items: [
      { to: '/dashboard', icon: LayoutGrid, label: 'Dashboard' },
      { to: '/live', icon: Video, label: 'Live Feed' },
      { to: '/incidents', icon: AlertTriangle, label: 'Incidents' },
      { to: '/assistant', icon: Sparkles, label: 'Friday', needs: ['assistant'] },
    ],
  },
  {
    label: 'Team',
    items: [
      { to: '/people', icon: Users, label: 'People', needs: ['manage_users', 'delegate_cameras'] },
      { to: '/duty', icon: ShieldCheck, label: 'Guards & duty' },
    ],
  },
  {
    label: 'System',
    items: [
      { to: '/cameras', icon: Camera, label: 'Cameras', needs: ['manage_cameras'] },
      { to: '/zones', icon: MapPin, label: 'Zones', needs: ['manage_zones'] },
      { to: '/logs', icon: ScrollText, label: 'Logs', needs: ['view_audit'] },
    ],
  },
]

export function Layout() {
  const [time, setTime] = useState(nowIST())
  const [alertNotice, setAlertNotice] = useState<AlertAllNotice | null>(null)
  const { user, logout } = useAuth()
  const { me, can } = useMe()
  const navigate = useNavigate()
  const { pathname } = useLocation()

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

  const visible = (item: NavItem) => !item.needs || !me || item.needs.some(can)

  return (
    <AnimatedSidebarProvider className="h-dvh min-h-0 overflow-hidden bg-black text-white">
      <AnimatedSidebar ariaLabel={`${APP_NAME} navigation`} collapsible="icon" className="min-h-0" panelClassName="h-full bg-[#0b0b0c]">
        <AnimatedSidebarHeader className="p-3 pb-2">
          <div className="flex min-h-11 items-center gap-3 overflow-hidden px-2">
            <div className="grid size-8 shrink-0 place-items-center rounded-[10px] border border-white/10 bg-white/10">
              <Shield className="size-4 text-white" aria-hidden="true" />
            </div>
            <span className="truncate text-sm font-semibold tracking-tight text-white group-data-[state=collapsed]/sidebar:hidden">
              {APP_NAME}
            </span>
          </div>
        </AnimatedSidebarHeader>

        <AnimatedSidebarContent className="px-2 pt-1">
          {NAV_GROUPS.map(group => {
            const items = group.items.filter(visible)
            if (items.length === 0) return null
            return (
              <AnimatedSidebarGroup key={group.label}>
                <AnimatedSidebarGroupLabel>{group.label}</AnimatedSidebarGroupLabel>
                <AnimatedSidebarGroupContent>
                  <AnimatedSidebarMenu>
                    {items.map(({ to, icon: Icon, label }) => (
                      <AnimatedSidebarMenuItem key={to}>
                        <AnimatedSidebarMenuButton
                          icon={<Icon className="size-4" />}
                          isActive={pathname === to || pathname.startsWith(`${to}/`)}
                          onSelect={() => navigate(to)}
                        >
                          {label}
                        </AnimatedSidebarMenuButton>
                      </AnimatedSidebarMenuItem>
                    ))}
                  </AnimatedSidebarMenu>
                </AnimatedSidebarGroupContent>
              </AnimatedSidebarGroup>
            )
          })}
        </AnimatedSidebarContent>

        <AnimatedSidebarFooter className="border-none p-3">
          <div className="flex min-h-11 items-center gap-3 overflow-hidden rounded-xl p-1">
            <span className="grid size-9 shrink-0 place-items-center rounded-full text-xs font-semibold" style={{ background: 'oklch(0.89 0.2 128)', color: 'oklch(0.24 0.06 135)' }}>
              {user?.name ? user.name.slice(0, 2).toUpperCase() : <UserIcon className="size-4" />}
            </span>
            <span className="min-w-0 flex-1 group-data-[state=collapsed]/sidebar:hidden">
              <span className="block truncate text-sm font-medium text-white">{user?.name ?? 'User'}</span>
              <span className="block truncate text-xs text-muted-foreground">{user?.role ?? ''}</span>
            </span>
          </div>
        </AnimatedSidebarFooter>
        <AnimatedSidebarRail />
      </AnimatedSidebar>

      <AnimatedSidebarInset className="h-dvh min-h-0 overflow-hidden bg-black">
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

        <div className="flex flex-col flex-1 min-w-0 min-h-0 bg-black">
          {/* Top bar */}
          <header
            className="flex items-center gap-4 px-5 border-b shrink-0"
            style={{
              height: '60px',
              background: '#0b0b0c',
              borderColor: 'rgba(255, 255, 255, 0.08)',
            }}
          >
            <AnimatedSidebarTrigger className="-ml-2 text-muted-foreground transition-colors hover:bg-white/5 hover:text-white">
              <PanelLeft aria-hidden="true" className="size-4" />
            </AnimatedSidebarTrigger>
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
              <DemoAlert />
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
      </AnimatedSidebarInset>
    </AnimatedSidebarProvider>
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
