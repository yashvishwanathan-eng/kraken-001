/**
 * pages/Overview.tsx — stub
 */
import { BarChart3 } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

export function OverviewPage() {
  return (
    <div>
      <PageHeader
        title="Overview"
        description="Incident analytics — trends, breakdown by type, and response times"
      />
      <EmptyState
        icon={BarChart3}
        title="Analytics dashboard coming next"
        description="Charts for incidents by type, incidents per day, open vs resolved ratio, and average guard response time will appear here."
      />
    </div>
  )
}
