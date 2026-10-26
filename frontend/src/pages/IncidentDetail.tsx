/**
 * pages/IncidentDetail.tsx — stub
 */
import { useParams } from 'react-router-dom'
import { FileVideo } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

export function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>()

  return (
    <div>
      <PageHeader
        title={`Incident #${id ?? '—'}`}
        description="Clip player, snapshot, confidence score, action timeline and response controls"
      />
      <EmptyState
        icon={FileVideo}
        title="Incident detail coming next"
        description="Video clip, snapshot viewer, timeline of calls / acknowledgements, and Acknowledge / Resolve / False-alarm controls will appear here."
      />
    </div>
  )
}
