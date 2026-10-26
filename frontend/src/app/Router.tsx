/**
 * app/Router.tsx
 * All routes for RingGuard. Protected routes require authenticated PIN session.
 */

import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './Layout'
import { LoginPage }          from '@/pages/Login'
import { LivePage }           from '@/pages/Live'
import { IncidentsPage }      from '@/pages/Incidents'
import { IncidentDetailPage }  from '@/pages/IncidentDetail'
import { AssistantPage }      from '@/pages/Assistant'
import { ZonesPage }          from '@/pages/Zones'
import { DashboardPage }      from '@/pages/Dashboard'
import { PeoplePage }         from '@/pages/People'
import { CamerasPage }        from '@/pages/Cameras'
import { DutyPage }           from '@/pages/Duty'
import { LogsPage }           from '@/pages/Logs'
import { useAuth }            from '@/lib/auth'

function ProtectedLayout() {
  const { isAuthenticated } = useAuth()
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }
  return <Layout />
}

export function Router() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard"       element={<DashboardPage />} />
        <Route path="/live"            element={<LivePage />} />
        <Route path="/incidents"       element={<IncidentsPage />} />
        <Route path="/incidents/:id"   element={<IncidentDetailPage />} />
        <Route path="/assistant"       element={<AssistantPage />} />
        <Route path="/people"          element={<PeoplePage />} />
        <Route path="/duty"            element={<DutyPage />} />
        <Route path="/cameras"         element={<CamerasPage />} />
        <Route path="/zones"           element={<ZonesPage />} />
        <Route path="/logs"            element={<LogsPage />} />
        {/* old addresses */}
        <Route path="/overview"        element={<Navigate to="/dashboard" replace />} />
        <Route path="/responders"      element={<Navigate to="/people" replace />} />
        <Route path="*"                element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
