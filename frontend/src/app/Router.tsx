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
import { RespondersPage }     from '@/pages/Responders'
import { ZonesPage }          from '@/pages/Zones'
import { OverviewPage }       from '@/pages/Overview'
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
        <Route index element={<Navigate to="/live" replace />} />
        <Route path="/live"            element={<LivePage />} />
        <Route path="/incidents"       element={<IncidentsPage />} />
        <Route path="/incidents/:id"   element={<IncidentDetailPage />} />
        <Route path="/assistant"       element={<AssistantPage />} />
        <Route path="/overview"        element={<OverviewPage />} />
        <Route path="/responders"      element={<RespondersPage />} />
        <Route path="/zones"           element={<ZonesPage />} />
        <Route path="*"                element={<Navigate to="/live" replace />} />
      </Route>
    </Routes>
  )
}
