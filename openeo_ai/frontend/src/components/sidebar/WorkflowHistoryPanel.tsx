import { History, Play, CheckCircle2, XCircle, Clock, RotateCcw, MessageSquare, Trash2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Workflow } from '@/types'
import type { ConversationSummary } from '@/hooks/useConversationHistory'
import { cn, formatDateTime } from '@/lib/utils'

interface WorkflowHistoryPanelProps {
  workflows: Workflow[]
  onReplay?: (workflow: Workflow) => void
  className?: string
  /** Persisted conversation summaries (most recent first). */
  conversations?: ConversationSummary[]
  /** Load a past conversation by ID. */
  onLoadConversation?: (id: string) => void
  /** Delete a past conversation by ID. */
  onDeleteConversation?: (id: string) => void
  /** Currently active conversation ID (highlighted in list). */
  activeConversationId?: string | null
}

export function WorkflowHistoryPanel({
  workflows,
  onReplay,
  className,
  conversations,
  onLoadConversation,
  onDeleteConversation,
  activeConversationId,
}: WorkflowHistoryPanelProps) {
  const hasConversations = conversations && conversations.length > 0

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <History className="h-4 w-4" />
          History
        </CardTitle>
      </CardHeader>
      <CardContent className="flex-1 p-0">
        <ScrollArea className="h-full">
          {/* Conversation History Section */}
          {hasConversations && (
            <div className="px-4 pt-3 pb-2">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <MessageSquare className="h-3 w-3" />
                Conversations
              </h3>
              <div className="mt-2 space-y-1.5">
                {conversations.map((conv) => (
                  <ConversationItem
                    key={conv.id}
                    conversation={conv}
                    isActive={conv.id === activeConversationId}
                    onLoad={onLoadConversation}
                    onDelete={onDeleteConversation}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Workflow History Section */}
          {hasConversations && workflows.length > 0 && (
            <div className="mx-4 border-t" />
          )}
          {workflows.length === 0 && !hasConversations ? (
            <div className="p-4 text-center text-sm text-muted-foreground">
              No history yet. Start by asking a question.
            </div>
          ) : workflows.length > 0 ? (
            <div className="px-4 pt-3 pb-4">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Play className="h-3 w-3" />
                Workflows
              </h3>
              <div className="mt-2 space-y-2">
                {workflows.map((workflow) => (
                  <WorkflowItem
                    key={workflow.id}
                    workflow={workflow}
                    onReplay={onReplay}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Conversation Item
// ---------------------------------------------------------------------------

function ConversationItem({
  conversation,
  isActive,
  onLoad,
  onDelete,
}: {
  conversation: ConversationSummary
  isActive: boolean
  onLoad?: (id: string) => void
  onDelete?: (id: string) => void
}) {
  return (
    <div
      className={cn(
        "group flex items-start gap-2 rounded-md border p-2 transition-colors",
        isActive
          ? "border-primary/40 bg-primary/5"
          : "border-transparent hover:bg-muted/50 hover:border-border"
      )}
    >
      <button
        className="flex flex-1 min-w-0 flex-col items-start text-left"
        onClick={() => onLoad?.(conversation.id)}
        title={`Load conversation: ${conversation.title}`}
      >
        <span className="truncate text-sm font-medium w-full">
          {conversation.title}
        </span>
        <span className="mt-0.5 text-xs text-muted-foreground">
          {conversation.messageCount} messages
          {' \u00b7 '}
          {formatDateTime(conversation.lastMessageAt)}
        </span>
        {conversation.visualizations && conversation.visualizations.length > 0 && (
          <span className="mt-0.5 text-xs text-muted-foreground/70">
            {conversation.visualizations.length} visualization{conversation.visualizations.length !== 1 ? 's' : ''}
          </span>
        )}
      </button>
      {onDelete && (
        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            onDelete(conversation.id)
          }}
          className="h-6 w-6 shrink-0 p-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
          title="Delete conversation"
        >
          <Trash2 className="h-3 w-3" />
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Workflow Item (unchanged from original)
// ---------------------------------------------------------------------------

function WorkflowItem({
  workflow,
  onReplay,
}: {
  workflow: Workflow
  onReplay?: (workflow: Workflow) => void
}) {
  const statusConfig = {
    idle: { icon: Clock, color: 'bg-muted text-muted-foreground', label: 'Idle' },
    running: { icon: Play, color: 'bg-blue-500/15 text-blue-700 dark:text-blue-400', label: 'Running' },
    completed: { icon: CheckCircle2, color: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400', label: 'Completed' },
    error: { icon: XCircle, color: 'bg-red-500/15 text-red-700 dark:text-red-400', label: 'Error' },
    stopped: { icon: XCircle, color: 'bg-muted text-muted-foreground', label: 'Stopped' },
  }

  const status = statusConfig[workflow.status]
  const StatusIcon = status.icon

  return (
    <div className="rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h4 className="truncate text-sm font-medium">{workflow.name}</h4>
          {workflow.description && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {workflow.description}
            </p>
          )}
        </div>
        <Badge variant="outline" className={cn("shrink-0 text-xs", status.color)}>
          <StatusIcon className="mr-1 h-3 w-3" />
          {status.label}
        </Badge>
      </div>

      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {formatDateTime(workflow.createdAt)}
        </span>

        {workflow.status === 'completed' && onReplay && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onReplay(workflow)}
            className="h-7 px-2 text-xs"
          >
            <RotateCcw className="mr-1 h-3 w-3" />
            Replay
          </Button>
        )}
      </div>

      {workflow.steps.length > 0 && (
        <div className="mt-2 border-t pt-2">
          <div className="flex flex-wrap gap-1">
            {workflow.steps.slice(0, 3).map((step) => (
              <span
                key={step.id}
                className={cn(
                  "rounded px-1.5 py-0.5 text-xs",
                  step.status === 'completed'
                    ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                    : step.status === 'error'
                    ? 'bg-red-500/15 text-red-700 dark:text-red-400'
                    : step.status === 'running'
                    ? 'bg-blue-500/15 text-blue-700 dark:text-blue-400'
                    : 'bg-muted text-muted-foreground'
                )}
              >
                {step.name}
              </span>
            ))}
            {workflow.steps.length > 3 && (
              <span className="text-xs text-muted-foreground">
                +{workflow.steps.length - 3} more
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
