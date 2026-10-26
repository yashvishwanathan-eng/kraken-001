/**
 * pages/Responders.tsx — stub
 */
import { Users } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

export function RespondersPage() {
  return (
    <div>
      <PageHeader
        title="Responders"
        description="Manage security guards, their zones, shifts and call order"
      />
      <EmptyState
        icon={Users}
        title="Responder management coming next"
        description="A table of guards with add / edit / delete controls. Fields include name, phone, language, zone, shift and call priority."
      />
    </div>
  )
}
