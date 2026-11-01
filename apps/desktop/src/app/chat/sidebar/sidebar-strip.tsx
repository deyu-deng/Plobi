import { type Dispatch, type ReactElement, type SetStateAction, useEffect, useRef, useState } from 'react'

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

/** 悬停开：够快不觉得迟钝，又不至于扫过整条就一路弹开。 */
export const STRIP_HOVER_OPEN_MS = 80
/** 离开后到真关之间的宽限：够跨过图标与面板之间那道缝。 */
export const STRIP_HOVER_CLOSE_MS = 240

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
  /** 只收元素：这里曾传过字符串 `'add'`，屏上就印出了「add」这个单词（用户 11-01）。 */
  icon: ReactElement
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

/**
 * 收起态的分类入口：悬停弹出这一类的 Agent，点一条就打开它的对话。
 *
 * 用户 11-01：「鼠标悬浮的弹窗会不停的闪烁，很难使用」。两根因都写在这里：
 *
 * ① `DropdownMenu` 默认 **modal** —— 一打开就把 `document.body` 的 pointer-events
 *    设成 `none`（Radix dismissable-layer，面板自己再抬回 `auto`），底下那只鼠标
 *    当场收到一次 pointerleave，于是「开 → 关 → 开」自己跟自己打。必须 `modal={false}`。
 * ② 开与关只能有一套判据：图标和面板是**同一个悬停区**——进任意一边取消待关，
 *    两边都离开才关。我们的定时器和 Radix 的 hoverable 桥并存一定打架，所以这里
 *    不靠 Radix 的悬停，进出都由这对指针事件说了算。
 *
 * 面板 portal 到 body，所以它自己的指针事件也得挂上，不然跨那道缝就被当成离开。
 */
function CategoryStripMenu({
  activeAgentId,
  group,
  label,
  onNavigateAgent,
  open,
  setOpenCategory
}: {
  activeAgentId: null | string
  group: AgentCategoryGroup<Agent>
  label: string
  onNavigateAgent: (id: string) => void
  open: boolean
  /** 整条只许开一个面板，所以开合都写在父上（见 SidebarStrip）。 */
  setOpenCategory: Dispatch<SetStateAction<null | string>>
}) {
  const { t } = useI18n()
  const timer = useRef<number | null>(null)
  /** 这一次是悬停开的（不是键盘/点击）：别把焦点从输入框抢进面板。 */
  const openedByHover = useRef(false)

  /** 只关自己这一类：上一类的待关定时器晚一步跑到，也不许把刚开的这一类关掉。 */
  const closeSelf = () => setOpenCategory(current => (current === group.category ? null : current))

  const cancelPending = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }

  useEffect(
    () => () => {
      // 组件被换掉（切会话/展开左栏）时别留一发待执行的定时器。
      if (timer.current !== null) {
        window.clearTimeout(timer.current)
      }
    },
    []
  )

  const handleEnter = () => {
    cancelPending()

    if (open) {
      return
    }

    timer.current = window.setTimeout(() => {
      timer.current = null
      openedByHover.current = true
      setOpenCategory(group.category)
    }, STRIP_HOVER_OPEN_MS)
  }

  const handleLeave = () => {
    cancelPending()
    timer.current = window.setTimeout(() => {
      timer.current = null
      closeSelf()
    }, STRIP_HOVER_CLOSE_MS)
  }

  /** Radix 自己要开合（点触发键 / 选一条 / Esc / 点了别处）时，待执行的定时器不再跑第二遍。 */
  const handleOpenChange = (next: boolean) => {
    cancelPending()

    if (next) {
      setOpenCategory(group.category)
    } else {
      closeSelf()
    }
  }

  /**
   * 悬停开的面板不许抢焦点：光标只是路过，用户可能正在输入框里打字——焦点一旦进了
   * 面板，接着打的字就进了方向键搜索，输入框当场失焦。键盘（Enter / ↓）打开时照常
   * 聚焦，那条路不置 `openedByHover`，所以面板里仍能上下走。
   *
   * Radix 的公开类型把 `onOpenAutoFocus` 划进了私有 props（`MenuContentImpl` 运行时
   * 照收），类型面上只能这样递进去。
   */
  const openFocusGuard = {
    onOpenAutoFocus: (event: Event) => {
      if (!openedByHover.current) {
        return
      }

      event.preventDefault()
      openedByHover.current = false
    }
  } as Record<string, unknown>

  return (
    <DropdownMenu modal={false} onOpenChange={handleOpenChange} open={open}>
      <DropdownMenuTrigger asChild>
        <button
          aria-label={label}
          className={cn(
            STRIP_BUTTON,
            group.agents.some(agent => agent.id === activeAgentId) &&
              'bg-(--ui-control-active-background) text-foreground'
          )}
          onPointerEnter={handleEnter}
          onPointerLeave={handleLeave}
          type="button"
        >
          <Codicon name={categoryIcon(group.category)} size="1rem" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-56"
        onPointerEnter={handleEnter}
        onPointerLeave={handleLeave}
        side="right"
        sideOffset={6}
        {...openFocusGuard}
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
  /**
   * 整条同时只许开一个分类面板。放在父上而不是每格自己存，是因为格子之间有 28px
   * 间距：A 的宽限期还没跑到就切到 B，两份面板会在同一个位置叠着闪一下。
   */
  const [openCategory, setOpenCategory] = useState<null | string>(null)

  return (
    <div className="flex h-full min-h-0 flex-col items-center px-1.5 py-2" data-slot="sidebar-strip">
      <div className="flex min-h-0 flex-1 flex-col items-center gap-px overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <StripButton icon={<Codicon name="add" size="1rem" />} label={t.console.newAgent} onClick={onCreateAgent} />

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
            open={openCategory === group.category}
            setOpenCategory={setOpenCategory}
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
