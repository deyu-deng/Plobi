import { useState } from 'react'

import type { Agent, AgentStatus } from '@/app/console/types'
import { secretaryShortName } from '@/app/desktop-controller-utils'
import { Codicon } from '@/components/ui/codicon'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { cn } from '@/lib/utils'
import type { SessionInfo } from '@/types/plobi'

import type { AgentCategory } from './agent-groups'
import { SidebarCount } from './chrome'
import { SidebarSessionRow } from './session-row'

const CATEGORY_STATUS_DOT: Record<AgentStatus, string> = {
  awaiting_approval: 'bg-amber-500',
  error: 'bg-destructive',
  idle: 'bg-muted-foreground/40',
  working: 'bg-emerald-500'
}

// `items-center` is load-bearing: the row is a fixed 28px flex box, and without
// it `align-items: stretch` pins fixed-size children to the top — the 8px status
// dot then sits ~6px above the label's optical centre.
const AGENT_ROW =
  'flex h-7 w-full items-center justify-start gap-2 rounded-md border border-transparent px-2 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors duration-100 ease-out [-webkit-app-region:no-drag] hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none'

const AGENT_ROW_ACTIVE =
  'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-foreground shadow-none hover:border-(--ui-stroke-tertiary)!'

export interface AgentCategoryGroupProps {
  activeAgentId: null | string
  activeSessionId: null | string
  agents: Agent[]
  category: AgentCategory
  /** Collapse state lives here; open by default so groups don't hide agents. */
  defaultOpen?: boolean
  /**
   * 裁定 38/39 (WP-AGENT-MOUTH): in the secretary shell an Agent row is one
   * mouth — never a session drawer, never a session count. The only call site
   * passes `hidesAgentSessions(level)`, so this is true for L1 *and* L2 there;
   * only a Plobi mainline consumer would pass false.
   */
  hideSessions?: boolean
  label: string
  onArchiveSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => void
  onNavigateAgent: (agentId: string) => void
  onResumeSession: (sessionId: string) => void
  /** Receives the raw session so the owner can reuse its pinned-id helper. */
  onTogglePin: (session: SessionInfo) => void
  /** Agent-profile -> that agent's sessions (already filtered upstream). */
  sessionsByProfile: Map<string, SessionInfo[]>
  workingSessionIdSet: Set<string>
}

/**
 * R-012: one collapsible sidebar category (项目/管家/事件/研究). Agents sort by
 * name upstream; each agent row expands inline to reveal its own sessions.
 */
export function AgentCategoryGroup({
  activeAgentId,
  activeSessionId,
  agents,
  category,
  defaultOpen = true,
  hideSessions = false,
  label,
  onArchiveSession,
  onDeleteSession,
  onNavigateAgent,
  onResumeSession,
  onTogglePin,
  sessionsByProfile,
  workingSessionIdSet
}: AgentCategoryGroupProps) {
  const [open, setOpen] = useState(defaultOpen)
  // Which agent's session drawer is expanded (one at a time keeps rows calm).
  const [openAgentId, setOpenAgentId] = useState<null | string>(null)
  const categoryKey = `agent-category-${category}`

  return (
    <div className="pb-0.5" data-testid={categoryKey}>
      <button
        aria-expanded={open}
        className="group/cat flex min-h-6 w-full items-center gap-1.5 rounded-md px-2 pt-1 text-left text-[0.6875rem] font-medium text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)"
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <SidebarCount>{agents.length}</SidebarCount>
        <DisclosureCaret
          className="shrink-0 text-(--ui-text-tertiary) opacity-0 transition group-hover/cat:opacity-100"
          open={open}
        />
      </button>

      {open && (
        <div className="flex flex-col gap-px pt-0.5">
          {agents.map(agent => {
            const agentSessions = hideSessions
              ? []
              : (sessionsByProfile.get((agent.profile ?? '').trim()) ?? [])
            const sessionsOpen = !hideSessions && openAgentId === agent.id
            // WP-UI-NO-INTERNALS: the rail shows the short label (「日程秘书」),
            // never a profile id like `l2-agenda`; the raw id lives in `title`.
            const shortName = secretaryShortName(agent.id, agent.name)

            return (
              <div key={agent.id}>
                <div
                  className={cn(
                    'group/agent flex min-w-0 items-center gap-0.5',
                    activeAgentId === agent.id && 'rounded-md'
                  )}
                >
                  <button
                    className={cn(AGENT_ROW, 'min-w-0 flex-1', activeAgentId === agent.id && AGENT_ROW_ACTIVE)}
                    onClick={() => onNavigateAgent(agent.id)}
                    title={agent.id}
                    type="button"
                  >
                    <span
                      aria-hidden="true"
                      className={cn('size-2 shrink-0 rounded-full', CATEGORY_STATUS_DOT[agent.status])}
                    />
                    <span className="min-w-0 flex-1 truncate">{shortName}</span>
                  </button>

                  {agentSessions.length > 0 && (
                    <button
                      aria-expanded={sessionsOpen}
                      aria-label={sessionsOpen ? undefined : `${shortName}: ${agentSessions.length}`}
                      className={cn(
                        'flex size-7 shrink-0 items-center justify-center rounded-md text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground',
                        activeAgentId === agent.id && 'text-foreground'
                      )}
                      onClick={() => setOpenAgentId(sessionsOpen ? null : agent.id)}
                      type="button"
                    >
                      <Codicon name={sessionsOpen ? 'chevron-down' : 'chevron-right'} size="0.75rem" />
                    </button>
                  )}
                </div>

                {sessionsOpen && (
                  <div className="ml-5 flex flex-col gap-px border-l border-(--sidebar-edge-border) py-0.5 pl-1.5">
                    {agentSessions.map(session => (
                      <SidebarSessionRow
                        isPinned={false}
                        isSelected={session.id === activeSessionId}
                        isWorking={workingSessionIdSet.has(session.id)}
                        key={session.id}
                        onArchive={() => onArchiveSession(session.id)}
                        onDelete={() => onDeleteSession(session.id)}
                        onPin={() => onTogglePin(session)}
                        onResume={() => onResumeSession(session.id)}
                        session={session}
                      />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
