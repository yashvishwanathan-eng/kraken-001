/**
 * pages/Zones.tsx — stub
 */
import { MapPin } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

export function ZonesPage() {
  return (
    <div>
      <PageHeader
        title="Zone Editor"
        description="Draw and edit restricted zones on each camera's snapshot"
      />
      <EmptyState
        icon={MapPin}
        title="Zone editor coming next"
        description="Pick a camera, then draw polygon zones on its snapshot image. Zones are saved to the backend and used for access-violation detection."
      />
    </div>
  )
}
