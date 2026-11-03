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
import { openConversation } from '@/store/conversation-view'

import { secretaryShortName } from '../../desktop-controller-utils'
import type { SidebarNavItem } from '../../types'

import { type AgentCategoryGroup, isNavigableAgent } from './agent-groups'
import { categoryIcon } from './category-icons'
import { useAgentExperts } from './expert-panel'
import { EXPERT_ROLES, latestRun, runsOfRole } from './expert-roles'
import { titleOf, whenText } from './external-sessions'
import { useExternalSessions } from './external-sessions-panel'
import { OffloadVendorIcon } from './offload-vendor-icon'

/** 左栏中段的两种内容（用户 11-01）：名单 / 外包。与 `index.tsx` 的 `MiddleView` 同义。 */
export type StripMiddleView = 'agents' | 'outsourced'

interface SidebarStripProps {
  activeAgentId: null | string
  /** 本 Agent 的 Capabilities / Artifacts（中间整页）；没打开 Agent 时按秘书算。 */
  agentActions: readonly { icon: string; id: string; label: string; onClick: () => void }[]
  footerItems: readonly StatusbarItem[]
  groups: AgentCategoryGroup<Agent>[]
  /** 收起前中段看的是哪一页——收起后中段就该是那一页的图标（用户 11-01）。 */
  middleView?: StripMiddleView
  /** 打开了某个 L2 才有「回秘书」这一行；位置要对上展开态的第二行。 */
  onBackToSecretary?: () => void
  onChangeMiddleView?: (next: StripMiddleView) => void
  onCreateAgent: () => void
  onNavigate: (item: SidebarNavItem) => void
  onNavigateAgent: (id: string) => void
  sharedItems: readonly SidebarNavItem[]
}

/**
 * 行高与展开态同一把尺子：展开态的每一行是 `h-7`（28px），这里也必须是 28px，
 * 否则「收起」时整条会往下错几像素，眼睛看得出来（用户 11-01：所有图标的高度和位置
 * 要和展开的时候对齐）。
 */
const STRIP_BUTTON =
  'grid size-7 shrink-0 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground disabled:cursor-default disabled:opacity-40'

const STRIP_BUTTON_ACTIVE = 'bg-(--ui-control-active-background) text-foreground'

/** 悬停开：够快不觉得迟钝，又不至于扫过整条就一路弹开。 */
export const STRIP_HOVER_OPEN_MS = 80
/** 离开后到真关之间的宽限：够跨过图标与面板之间那道缝。 */
export const STRIP_HOVER_CLOSE_MS = 240

/**
 * 分隔线要与展开态那条同高。展开态是 `<div className="my-2 h-px">`（8+1+8 = 17px）；
 * 收起态这一列是 `gap-px`，上下各还会多出 1px 的 gap，所以这里用 7px 把它抵回来
 * （7+1+7 + 两侧各 1px = 17px）。差 1px 眼睛就能看出来——用户 11-01 要的是逐行对齐。
 */
function StripSep() {
  return <div aria-hidden="true" className="my-[0.4375rem] h-px w-5 shrink-0 bg-(--ui-stroke-tertiary)" />
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
        className={cn(STRIP_BUTTON, active && STRIP_BUTTON_ACTIVE)}
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
 * 收起态的**无文字开关**（用户 11-01：「切换组件也要有收起态，且可以交互。变成一个
 * 没有文字的单纯开关」）。
 *
 * 40px 宽放不下「图标 + 名词」那种胶囊，所以只剩一个能拨的开关：左 = 名单，右 = 外包。
 * 屏上不写一个字，名字进 `aria-label`——它给读屏，不给眼睛。
 */
function CollapsedMiddleSwitch({
  onChange,
  value
}: {
  onChange: (next: StripMiddleView) => void
  value: StripMiddleView
}) {
  const { t } = useI18n()
  const outsourced = value === 'outsourced'

  return (
    <button
      aria-checked={outsourced}
      aria-label={`${t.console.tabAgents} / ${t.console.tabOutsourced}`}
      className="relative flex h-4 w-7 shrink-0 items-center rounded-full bg-(--ui-bg-secondary) px-0.5 transition-colors hover:bg-(--ui-control-hover-background)"
      onClick={() => onChange(outsourced ? 'agents' : 'outsourced')}
      role="switch"
      type="button"
    >
      <span
        className={cn(
          'size-3 rounded-full bg-(--ui-text-tertiary) transition-transform duration-150 ease-out',
          outsourced ? 'translate-x-3' : 'translate-x-0'
        )}
      />
    </button>
  )
}

/**
 * 收起态的悬停菜单外壳：图标 → 右边弹一格，进出都算「还在里面」。
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
function StripHoverMenu({
  active,
  children,
  icon,
  label,
  menuClassName = 'w-56',
  menuKey,
  open,
  setOpenKey
}: {
  active?: boolean
  children: React.ReactNode
  icon: ReactElement
  label: string
  menuClassName?: string
  menuKey: string
  open: boolean
  /** 整条只许开一个面板，所以开合都写在父上（见 SidebarStrip）。 */
  setOpenKey: Dispatch<SetStateAction<null | string>>
}) {
  const timer = useRef<number | null>(null)
  /** 这一次是悬停开的（不是键盘/点击）：别把焦点从输入框抢进面板。 */
  const openedByHover = useRef(false)

  /** 只关自己这一格：上一格的待关定时器晚一步跑到，也不许把刚开的那格关掉。 */
  const closeSelf = () => setOpenKey(current => (current === menuKey ? null : current))

  const cancelPending = () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }

  useEffect(
    () => () => {
      // 组件被换掉（切视图/展开左栏）时别留一发待执行的定时器。
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
      setOpenKey(menuKey)
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
      setOpenKey(menuKey)
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
          className={cn(STRIP_BUTTON, active && STRIP_BUTTON_ACTIVE)}
          onPointerEnter={handleEnter}
          onPointerLeave={handleLeave}
          type="button"
        >
          {icon}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className={menuClassName}
        onPointerEnter={handleEnter}
        onPointerLeave={handleLeave}
        side="right"
        sideOffset={6}
        {...openFocusGuard}
      >
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 收起态的分类入口：悬停弹出这一类的 Agent，点一条就打开它的对话。 */
function CategoryStripMenu({
  activeAgentId,
  group,
  label,
  onNavigateAgent,
  open,
  setOpenKey
}: {
  activeAgentId: null | string
  group: AgentCategoryGroup<Agent>
  label: string
  onNavigateAgent: (id: string) => void
  open: boolean
  setOpenKey: Dispatch<SetStateAction<null | string>>
}) {
  const { t } = useI18n()

  return (
    <StripHoverMenu
      active={group.agents.some(agent => agent.id === activeAgentId)}
      icon={<Codicon name={categoryIcon(group.category)} size="1rem" />}
      label={label}
      menuKey={`cat:${group.category}`}
      open={open}
      setOpenKey={setOpenKey}
    >
      <div className="px-2 pb-1 pt-1.5 text-[0.68rem] font-medium text-(--ui-text-quaternary)">{label}</div>
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
    </StripHoverMenu>
  )
}

/**
 * 收起态的「外包」中段：**专家** 与 **外包** 两枚图标，各自悬停出一格浮窗，
 * 不展开左栏也能直接挑一条对话（用户 11-01）。
 *
 * 数据两路都在这儿取（专家名单 / 外部会话），所以这个组件只在「外包」这一页挂载——
 * 停在名单那一页时不该为它轮询。
 */
function OutsourcedStripMiddle({
  agentId,
  openKey,
  setOpenKey
}: {
  agentId: string
  openKey: null | string
  setOpenKey: Dispatch<SetStateAction<null | string>>
}) {
  const { t } = useI18n()
  const c = t.console
  const experts = useAgentExperts(agentId)
  const { data } = useExternalSessions(agentId)

  // 收在浮窗里的会话：只看活着的、L2 没判过无关的；新的在上面。
  const sessions = (data?.sessions ?? [])
    .filter(session => !session.archived && session.related !== false)
    .slice()
    .sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)))

  return (
    <>
      <StripHoverMenu
        active={experts.some(run => run.status === 'running')}
        icon={<Codicon name="person" size="1rem" />}
        label={c.expertsTitle}
        menuKey="experts"
        open={openKey === 'experts'}
        setOpenKey={setOpenKey}
      >
        <div className="px-2 pb-1 pt-1.5 text-[0.68rem] font-medium text-(--ui-text-quaternary)">
          {c.expertsTitle}
        </div>
        {EXPERT_ROLES.map(role => {
          const latest = latestRun(runsOfRole(experts, role.key))

          return (
            <DropdownMenuItem
              className="gap-2"
              disabled={!latest}
              key={role.key}
              onSelect={() =>
                latest &&
                openConversation({
                  agentId,
                  kind: 'expert',
                  running: latest.status === 'running',
                  sessionId: latest.id,
                  title: latest.goal || latest.id
                })}
            >
              <span className="grid size-4 shrink-0 place-items-center text-(--ui-text-tertiary)">
                <Codicon name={role.icon} size="0.875rem" />
              </span>
              <span className="min-w-0 flex-1 truncate">{c[role.labelKey]}</span>
              {latest?.status === 'running' ? (
                <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-(--ui-accent)" />
              ) : null}
            </DropdownMenuItem>
          )
        })}
      </StripHoverMenu>

      <StripHoverMenu
        icon={<Codicon name="send" size="1rem" />}
        label={c.offloadsTitle}
        menuKey="outsourced"
        open={openKey === 'outsourced'}
        setOpenKey={setOpenKey}
      >
        <div className="px-2 pb-1 pt-1.5 text-[0.68rem] font-medium text-(--ui-text-quaternary)">
          {c.offloadsTitle}
        </div>
        {sessions.length === 0 ? (
          <div className="px-2 pb-2 text-xs text-(--ui-text-tertiary)">{c.externalSessionsEmpty}</div>
        ) : (
          sessions.slice(0, 20).map(session => (
            <DropdownMenuItem
              className="gap-2"
              key={`${session.vendor}-${session.id}`}
              onSelect={() =>
                openConversation({
                  agentId,
                  kind: 'external',
                  sendable: session.sendable,
                  sessionId: session.id,
                  title: session.title || session.id.slice(0, 8),
                  vendor: session.vendor
                })}
            >
              <OffloadVendorIcon vendor={session.vendor} />
              <span className="min-w-0 flex-1 truncate">{titleOf(session)}</span>
              <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
                {whenText(session.updated_at)}
              </span>
            </DropdownMenuItem>
          ))
        )}
      </StripHoverMenu>
    </>
  )
}

/**
 * 收起态的左栏：40px 宽，只放一枚枚矢量图标（用户 10-31）。
 *
 * 11-01 补的是**对齐**：自上而下每一格都按展开态那把尺子排（`pt-2` 起、每行 28px、
 * 分隔线 `my-2`），底部 Capabilities / Artifacts 与收敛区各自贴在自己的底边上——
 * 收起前后它们不该跳位置。中段取决于收起时看的是哪一页：名单 → 五类 Agent；
 * 外包 → 专家与外包两枚图标（各带浮窗）。
 */
export function SidebarStrip({
  activeAgentId,
  agentActions,
  footerItems,
  groups,
  middleView = 'agents',
  onBackToSecretary,
  onChangeMiddleView,
  onCreateAgent,
  onNavigate,
  onNavigateAgent,
  sharedItems
}: SidebarStripProps) {
  const { t } = useI18n()
  /**
   * 整条同时只许开一个面板。放在父上而不是每格自己存，是因为格子之间有 28px
   * 间距：A 的宽限期还没跑到就切到 B，两份面板会在同一个位置叠着闪一下。
   */
  const [openKey, setOpenKey] = useState<null | string>(null)

  // 没有 L2 就没有外包这一页（那是「这一个 Agent 把活派给了谁」）。
  const outsourced = middleView === 'outsourced' && Boolean(activeAgentId)

  return (
    <div className="flex h-full min-h-0 flex-col items-center pt-2" data-slot="sidebar-strip">
      <div className="flex min-h-0 w-full flex-1 flex-col items-center gap-px overflow-y-auto overflow-x-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <StripButton icon={<Codicon name="add" size="1rem" />} label={t.console.newAgent} onClick={onCreateAgent} />

        {/* 展开态里「回秘书」是第二行；收起态也占这一格，下面的图标才不会整体上移。 */}
        {onBackToSecretary ? (
          <StripButton
            icon={<Codicon name="arrow-left" size="1rem" />}
            label={t.console.backToSecretary}
            onClick={onBackToSecretary}
          />
        ) : null}

        {sharedItems.map(item => (
          <StripButton
            icon={<item.icon className="size-4 leading-none" />}
            key={item.id}
            label={t.sidebar.nav[item.id] ?? item.label}
            onClick={() => onNavigate(item)}
          />
        ))}

        <StripSep />

        {/* 分段控件收起后就是这个开关：整块与展开态那枚胶囊同高（24px + 6px 下留白），
            否则它下面的图标会整体上移 8px。 */}
        {activeAgentId && onChangeMiddleView ? (
          <div className="flex h-6 w-full shrink-0 items-center justify-center pb-1.5">
            <CollapsedMiddleSwitch onChange={onChangeMiddleView} value={middleView} />
          </div>
        ) : null}

        {outsourced && activeAgentId ? (
          <OutsourcedStripMiddle agentId={activeAgentId} openKey={openKey} setOpenKey={setOpenKey} />
        ) : (
          groups.map(group => (
            <CategoryStripMenu
              activeAgentId={activeAgentId}
              group={group}
              key={group.category}
              label={t.console.agentCategories[group.category] ?? group.category}
              onNavigateAgent={onNavigateAgent}
              open={openKey === `cat:${group.category}`}
              setOpenKey={setOpenKey}
            />
          ))
        )}
      </div>

      {/* 本 Agent 的两个入口：与展开态同一块（border-t + py-1），所以收起前后不跳。 */}
      {agentActions.length > 0 ? (
        <div className="w-full shrink-0 border-t border-(--ui-stroke-tertiary) px-1 py-1">
          <div className="flex flex-col items-center gap-px">
            {agentActions.map(action => (
              <StripButton
                icon={<Codicon name={action.icon} size="1rem" />}
                key={action.id}
                label={action.label}
                onClick={action.onClick}
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="w-full shrink-0 border-t border-(--ui-stroke-tertiary) px-1.5 py-1">
        <StatusbarControls dock inline items={footerItems} />
      </div>
    </div>
  )
}
