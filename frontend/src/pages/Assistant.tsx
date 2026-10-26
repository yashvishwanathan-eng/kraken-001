/**
 * pages/Assistant.tsx — stub
 */
import { MessageSquare } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'

export function AssistantPage() {
  return (
    <div>
      <PageHeader
        title="AI Assistant"
        description="Ask questions about incidents by typing or speaking"
      />
      <EmptyState
        icon={MessageSquare}
        title="Chat interface coming next"
        description="A chat panel with text input, microphone recording, and AI-generated answers with linked incident cards will appear here."
      />
    </div>
  )
}
