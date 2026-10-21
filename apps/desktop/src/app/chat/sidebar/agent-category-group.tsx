import { useStore } from '@nanostores/react'
import { useState } from 'react'

import type { Agent, AgentStatus } from '@/app/console/types'
import { secretaryShortName } from '@/app/desktop-controller-utils'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import { $sidebarAgentCategoryCollapsedIds, toggleSidebarAgentCategoryCollapsed } from '@/store/layout'
import type { SessionInfo } from '@/types/plobi'

import { AgentActionsMenu } from './agent-actions-menu'
import type { AgentCategory } from './agent-groups'
import { isNavigableAgent } from './agent-groups'
import {
  SIDEBAR_ROW_DOT_SIZE,
  SidebarRowBody,
  SidebarRowLabel,
  SidebarRowLead,
  SidebarRowShell,
  SidebarSectionHeader
} from './chrome'
import { SidebarSessionRow } from './session-row'

const CATEGORY_STATUS_DOT: Record<AgentStatus, string> = {
  awaiting_approval: 'bg-amber-500',
  error: 'bg-destructive',
  idle: 'bg-muted-foreground/40',
  working: 'bg-emerald-500'
}

export interface AgentCategoryGroupProps {
  activeAgentId: null | string
  activeSessionId: null | string
  agents: Agent[]
  category: AgentCategory
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
 *
 * The row and the fold header are the **same** chrome every other sidebar row
 * uses (`chrome.tsx`) — this column used to carry its own copy of the row
 * classes and its own header, so an agent row looked and folded differently
 * from a session or project row sitting right above it.
 */
export function AgentCategoryGroup({
  activeAgentId,
  activeSessionId,
  agents,
  category,
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
  // Which agent's session drawer is expanded (one at a time keeps rows calm).
  const [openAgentId, setOpenAgentId] = useState<null | string>(null)
  const collapsedIds = useStore($sidebarAgentCategoryCollapsedIds)
  const open = !collapsedIds.includes(category)

  return (
    <div className="pb-0.5" data-testid={`agent-category-${category}`}>
      <SidebarSectionHeader
        label={label}
        meta={agents.length}
        onToggle={() => toggleSidebarAgentCategoryCollapsed(category)}
        open={open}
      />

      {open && (
        <div className="flex flex-col gap-px pt-0.5">
          {agents.map(agent => {
            const agentSessions = hideSessions
              ? []
              : (sessionsByProfile.get((agent.profile ?? '').trim()) ?? [])

            const sessionsOpen = !hideSessions && openAgentId === agent.id
            // 数据条目（没有 profile 的行）不可点：它没有自己的会话，点进去只会是一张空壳，
            // 而以前那一下点击正是让主进程去起一个不存在的 profile。
            const navigable = isNavigableAgent(agent)
            // WP-UI-NO-INTERNALS: the rail shows the short label (「日程秘书」),
            // never a profile id like `l2-agenda`; the raw id lives in `title`.
            const shortName = secretaryShortName(agent.id, agent.name)
            const isActive = activeAgentId === agent.id

            return (
              <div key={agent.id}>
                <SidebarRowShell
                  actions={
                    <>
                      {agentSessions.length > 0 && (
                        <button
                          aria-expanded={sessionsOpen}
                          aria-label={sessionsOpen ? undefined : `${shortName}: ${agentSessions.length}`}
                          className={cn(
                            'flex size-5 shrink-0 items-center justify-center rounded-md text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground',
                            isActive && 'text-foreground'
                          )}
                          onClick={() => setOpenAgentId(sessionsOpen ? null : agent.id)}
                          type="button"
                        >
                          <Codicon name={sessionsOpen ? 'chevron-down' : 'chevron-right'} size="0.75rem" />
                        </button>
                      )}
                      {/* 「三个点」：这一格只放这个 Agent 自己的事（打开 / 能力 /
                          归档）。profile 那个概念已退役，菜单里不再有它。 */}
                      <AgentActionsMenu agent={agent} onNavigate={onNavigateAgent} />
                    </>
                  }
                  className={cn(
                    'group row-hover',
                    isActive && 'bg-(--ui-row-active-background)',
                    !navigable && 'cursor-default opacity-70'
                  )}
                >
                  <SidebarRowBody
                    className="z-0"
                    disabled={!navigable}
                    onClick={() => navigable && onNavigateAgent(agent.id)}
                    title={navigable ? agent.id : (agent.notNavigableReason || agent.id)}
                  >
                    <SidebarRowLead>
                      <span
                        aria-hidden="true"
                        className={cn(SIDEBAR_ROW_DOT_SIZE, 'rounded-full', CATEGORY_STATUS_DOT[agent.status])}
                      />
                    </SidebarRowLead>
                    <SidebarRowLabel>{shortName}</SidebarRowLabel>
                  </SidebarRowBody>
                </SidebarRowShell>

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
