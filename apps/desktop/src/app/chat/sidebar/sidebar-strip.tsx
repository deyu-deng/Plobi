import { type ReactNode, useRef, useState } from 'react'

import type { Agent } from '@/app/console/types'
import { StatusbarControls, type StatusbarItem } from '@/app/shell/statusbar-controls'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { secretaryShortName } from '../../desktop-controller-utils'
import type { SidebarNavItem } from '../../types'

import { type AgentCategoryGroup, isNavigableAgent } from './agent-groups'
import { categoryIcon } from './category-icons'

interface SidebarStripProps {
  activeAgentId: null | string
  /** 本 Agent 的 Capabilities / Artifacts（中间整页）；没打开 Agent 时按秘书算。 */
  agentActions: readonly { icon: string; id: string; label: string; onClick: () => void }[]
  footerItems: readonly StatusbarItem[]
  groups: AgentCategoryGroup<Agent>[]
  onCreateAgent: () => void
  onNavigate: (item: SidebarNavItem) => void
  onNavigateAgent: (id: string) => void
  sharedItems: readonly SidebarNavItem[]
}

const STRIP_BUTTON =
  'grid size-7 shrink-0 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground disabled:cursor-default disabled:opacity-40'

function StripSep() {
  return <div aria-hidden="true" className="my-1.5 h-px w-5 shrink-0 bg-(--ui-stroke-tertiary)" />
}

function StripButton({
  active,
  disabled,
  icon,
  label,
  onClick
}: {
  active?: boolean
  disabled?: boolean
  icon: ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <Tip label={label}>
      <button
        aria-label={label}
        aria-pressed={active || undefined}
        className={cn(STRIP_BUTTON, active && 'bg-(--ui-control-active-background) text-foreground')}
        disabled={disabled}
        onClick={onClick}
        type="button"
      >
        {icon}
      </button>
    </Tip>
  )
}

/** 收起态的分类入口：悬停弹出这一类的 Agent，点一条就打开它的对话。 */
function CategoryStripMenu({
  activeAgentId,
  group,
  label,
  onNavigateAgent
}: {
  activeAgentId: null | string
  group: AgentCategoryGroup<Agent>
  label: string
  onNavigateAgent: (id: string) => void
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const timer = useRef<number | null>(null)

  const clearTimer = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }
  const scheduleOpen = () => {
    clearTimer()
    timer.current = window.setTimeout(() => setOpen(true), 120)
  }
  const scheduleClose = () => {
    clearTimer()
    timer.current = window.setTimeout(() => setOpen(false), 180)
  }

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <DropdownMenuTrigger asChild>
        <button
          aria-label={label}
          className={cn(
            STRIP_BUTTON,
            group.agents.some(agent => agent.id === activeAgentId) &&
              'bg-(--ui-control-active-background) text-foreground'
          )}
          onPointerEnter={scheduleOpen}
          onPointerLeave={scheduleClose}
          type="button"
        >
          <Codicon name={categoryIcon(group.category)} size="1rem" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-56"
        onPointerEnter={clearTimer}
        onPointerLeave={scheduleClose}
        side="right"
        sideOffset={6}
      >
        <div className="px-2 pb-1 pt-1.5 text-[0.68rem] font-medium text-(--ui-text-quaternary)">
          {label}
        </div>
        {group.agents.length === 0 ? (
          <div className="px-2 pb-2 text-xs text-(--ui-text-tertiary)">{t.console.agentsEmptyDesc}</div>
        ) : (
          group.agents.map(agent => {
            const navigable = isNavigableAgent(agent)

            return (
              <DropdownMenuItem
                className="gap-2"
                disabled={!navigable}
                key={agent.id}
                onSelect={() => navigable && onNavigateAgent(agent.id)}
              >
                {agent.icon ? (
                  <span
                    aria-hidden="true"
                    className="flex size-4 shrink-0 items-center justify-center"
                    style={agent.color ? { color: agent.color } : undefined}
                  >
                    <Codicon name={agent.icon} size="0.875rem" />
                  </span>
                ) : (
                  <span
                    aria-hidden="true"
                    className="size-1.5 shrink-0 rounded-full bg-muted-foreground/40"
                    style={agent.color ? { backgroundColor: agent.color } : undefined}
                  />
                )}
                <span className="min-w-0 flex-1 truncate">{secretaryShortName(agent.id, agent.name)}</span>
                {agent.working ? <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" /> : null}
              </DropdownMenuItem>
            )
          })
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * 收起态的左栏：40px 宽，只放一枚矢量图标（用户 10-31）。
 * 自上而下：新建 → 共享（知识库/消息平台/日记）→ 五类 Agent → 本 Agent 的动作 →
 * 最底下收敛区（40px 放不下任何一枚，只剩「⋯」）。
 */
export function SidebarStrip({
  activeAgentId,
  agentActions,
  footerItems,
  groups,
  onCreateAgent,
  onNavigate,
  onNavigateAgent,
  sharedItems
}: SidebarStripProps) {
  const { t } = useI18n()

  return (
    <div className="flex h-full min-h-0 flex-col items-center px-1.5 py-2" data-slot="sidebar-strip">
      <div className="flex min-h-0 flex-1 flex-col items-center gap-px overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <StripButton icon="add" label={t.console.newAgent} onClick={onCreateAgent} />

        <StripSep />

        {sharedItems.map(item => (
          <StripButton
            icon={<item.icon className="size-4 leading-none" />}
            key={item.id}
            label={t.sidebar.nav[item.id] ?? item.label}
            onClick={() => onNavigate(item)}
          />
        ))}

        <StripSep />

        {groups.map(group => (
          <CategoryStripMenu
            activeAgentId={activeAgentId}
            group={group}
            key={group.category}
            label={t.console.agentCategories[group.category] ?? group.category}
            onNavigateAgent={onNavigateAgent}
          />
        ))}

        <StripSep />

        {agentActions.map(action => (
          <StripButton
            icon={<Codicon name={action.icon} size="1rem" />}
            key={action.id}
            label={action.label}
            onClick={action.onClick}
          />
        ))}
      </div>

      <div className="w-full shrink-0 border-t border-(--ui-stroke-tertiary) pt-1">
        <StatusbarControls dock inline items={footerItems} />
      </div>
    </div>
  )
}
