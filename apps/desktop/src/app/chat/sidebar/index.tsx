import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { StatusbarControls, type StatusbarItem } from '@/app/shell/statusbar-controls'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem
} from '@/components/ui/sidebar'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { SessionInfo } from '@/plobi'
import { $panesFlipped, $sidebarOpen, pinSession } from '@/store/layout'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { pickProjectFolder } from '@/store/projects'
import { $selectedStoredSessionId, $sessions, $workingSessionIds, sessionPinId } from '@/store/session'

import type { Agent } from '../../console/types'
import { createAgent } from '../../console/api'
import { $consoleAgents, $consoleAgentsError, $consoleAgentsLoading, refreshConsoleAgents } from '../../console/store/agents'
import {
  agentRoute,
  type AppView,
  ARTIFACTS_ROUTE,
  HOME_ROUTE,
  MESSAGING_ROUTE,
  type ShellLevel,
  DIARY_ROUTE,
  KNOWLEDGE_ROUTE,
  SKILLS_ROUTE
} from '../../routes'
import type { SidebarNavItem } from '../../types'

import { AgentCategoryGroup } from './agent-category-group'
import {
  AGENT_CATEGORY_PREFERENCE,
  type AgentCategoryGroup as AgentCategoryGroupData,
  agentProfileIdSet,
  groupAgentsByCategory,
  isL1SessionProfile
} from './agent-groups'
import { AgentOffloadPanel } from './agent-offload-panel'
import { categoryIcon } from './category-icons'
import { SidebarStrip } from './sidebar-strip'
import {
  AGENT_COLOR_CHOICES,
  AGENT_ICON_CHOICES,
  type AgentCategory,
  buildCreateAgentBody,
  categoryNeedsProjectPath,
  creatableCategoryOptions,
  createAgentErrorMessage,
  DEFAULT_AGENT_CATEGORY,
  isCreateAgentCategoryBlocked,
  isCreateAgentSubmitDisabled
} from './create-agent'
import { sidebarNavForLevel } from './nav-level'
import { ProjectDialog } from './project-dialog'

export const SIDEBAR_NAV: SidebarNavItem[] = [
  {
    id: 'skills',
    label: '',
    icon: props => <Codicon name="symbol-misc" {...props} />,
    route: SKILLS_ROUTE
  },
  { id: 'messaging', label: '', icon: props => <Codicon name="comment" {...props} />, route: MESSAGING_ROUTE },
  { id: 'artifacts', label: '', icon: props => <Codicon name="files" {...props} />, route: ARTIFACTS_ROUTE },
  { id: 'knowledge', label: '', icon: props => <Codicon name="book" {...props} />, route: KNOWLEDGE_ROUTE },
  { id: 'diary', label: '', icon: props => <Codicon name="edit" {...props} />, route: DIARY_ROUTE }
]

// NAV row chrome (the top icon rail): height/typography live here so the
// secretary-specific rows (new-agent / back-to-secretary) match the base rows
// exactly instead of re-deriving them inline. `[-webkit-app-region:no-drag]`:
// these rows sit directly under the titlebar's drag strips (app-shell.tsx) with
// only 6px clearance — drag regions win hit-testing over DOM, so the carve-out
// keeps the top rows clickable (same idiom as USER_BUBBLE_BASE_CLASS).
// `items-center`: same latent bug as AGENT_ROW — without it the fixed-size
// nav icon stretch-aligns to the top of the 28px row.
const NAV_BUTTON =
  'flex h-7 w-full items-center justify-start gap-2 rounded-md border border-transparent px-2 text-left text-[0.8125rem] font-medium text-(--ui-text-secondary) transition-colors duration-100 ease-out [-webkit-app-region:no-drag] hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none'

const NAV_BUTTON_ACTIVE =
  'border-(--ui-stroke-tertiary) bg-(--ui-control-active-background) text-foreground shadow-none hover:border-(--ui-stroke-tertiary)!'

const NAV_BUTTON_DISABLED = 'cursor-default hover:border-transparent hover:bg-transparent hover:text-inherit'
// `leading-none`: Codicon renders an icon-FONT <i>, whose glyph sits on the
// font baseline inside a ~1.5em line box — in a 28px row that reads as
// "icon a pixel or two high" (the L1 「New agent」 + was visibly off-centre).
// Same idiom the rest of the shell already uses: `chrome.tsx` applies
// `[&_.codicon]:leading-none` and `codiconIcon()` bakes `leading-none` in.
const NAV_ICON = 'size-4 shrink-0 leading-none text-[color-mix(in_srgb,currentColor_72%,transparent)]'

interface ChatSidebarProps extends React.ComponentProps<typeof Sidebar> {
  /** 底部收敛区那一排（系统工具 + 状态图标）：10-31 起住在左栏底部。 */
  footerItems: readonly StatusbarItem[]
  currentView: AppView
  level: ShellLevel | null
  onNavigate: (item: SidebarNavItem) => void
  onResumeSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => void
  onArchiveSession: (sessionId: string) => void
}

export function ChatSidebar({
  footerItems,
  currentView,
  level,
  onNavigate,
  onResumeSession,
  onDeleteSession,
  onArchiveSession
}: ChatSidebarProps) {
  const { t } = useI18n()
  const s = t.sidebar
  const c = t.console
  const navigate = useNavigate()

  // WP-B: the sidebar's left rail branches on the route's secretary identity
  // (ARCH-UI-MASTER §4.3). Only the rail CONTENT differs — the shell and center
  // column don't. `level` is a pure function of the pathname (resolved by the
  // controller) so the sidebar never owns routing state beyond this prop.
  const l1 = level === 'l1'
  const l2Id = level && typeof level === 'object' ? level.l2 : null

  // The Agents list (every shell: L1 / L2 / overlay pages). Reads the shared
  // `console/store/agents` cache — a render cache whose truth is the backend
  // `GET /api/agents` (WP-BE-1).
  const secretaryAgents = useStore($consoleAgents)
  // 换一个 Agent 就退出「外包」那一页（它不是全局开关，也不改导航）。
  useEffect(() => {
    setShowOffload(false)
  }, [l2Id])
  const secretaryAgentsLoading = useStore($consoleAgentsLoading)
  // 读失败与「名册真的空」必须长成两张脸：前者要留着上一栏并给一句人话＋重试，
  // 后者才是空态。2026-10-26 那次整栏消失就是因为这两件事在界面上无法区分。
  const secretaryAgentsError = useStore($consoleAgentsError)

  // 左栏现在只有 Agents 一张列表（裁定 38/39 终态：会话/profile 不再是产品
  // 概念），overlay 页也常驻 → 名册在挂载时拉一次。
  useEffect(() => {
    void refreshConsoleAgents()
  }, [])

  // 左栏那颗 Capabilities 指向**当前 Agent 自己的**那一页（2026-10-20 用户裁定 C）。
  // L2 → 它名册行的 id；L1 也是 Agent，用当前生效 profile 反查它那一行。
  // 解析不到就退回不带参数的 /skills（那是全局真源，只在「设置 → Capabilities」里用）。
  //
  // 10-31：键换成**名册 id**——profile 是裁 42 / ADR-0013 要拆掉的概念，
  // 不许再从产品面的 URL 上回来（用户：「不要让 profile 的概念回归」）。
  const liveProfile = useStore($activeGatewayProfile)

  const navItems = useMemo(() => {
    const base = sidebarNavForLevel(SIDEBAR_NAV, { l1, l2Id })

    const scopedAgent =
      (l2Id ? secretaryAgents.find(agent => agent.id === l2Id) : null) ??
      (liveProfile
        ? secretaryAgents.find(
            agent => normalizeProfileKey(agent.profile ?? '') === normalizeProfileKey(liveProfile)
          )
        : null) ??
      null

    return base.map(item =>
      item.id === 'skills' && scopedAgent
        ? { ...item, route: `${SKILLS_ROUTE}?agent=${encodeURIComponent(scopedAgent.id)}` }
        : item
    )
  }, [l1, l2Id, liveProfile, secretaryAgents])

  const sidebarOpen = useStore($sidebarOpen)
  // 收起态不再归零：同一个栏换成 40px 图标条（用户 10-31）。
  const contentVisible = sidebarOpen
  const panesFlipped = useStore($panesFlipped)
  const selectedSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const workingSessionIds = useStore($workingSessionIds)

  const workingSessionIdSet = useMemo(() => new Set(workingSessionIds), [workingSessionIds])

  const [showOffload, setShowOffload] = useState(false)
  const [createAgentOpen, setCreateAgentOpen] = useState(false)
  const [createAgentName, setCreateAgentName] = useState('')
  const [createAgentBusy, setCreateAgentBusy] = useState(false)
  const [createAgentError, setCreateAgentError] = useState<null | string>(null)
  const [createAgentCategory, setCreateAgentCategory] = useState<AgentCategory>(DEFAULT_AGENT_CATEGORY)
  // WP-STUDIO (裁定 36.1): the workspace folder(s) for a projects/research/courses
  // agent. WP-AGENT-CREATE-UPGRADE（用户 10-16）：单值改清单——「一个 Agent 肯定
  // 也要可以添加多个文件夹」。主文件夹 = 第一个。
  const [createAgentPaths, setCreateAgentPaths] = useState<string[]>([])
  // WP-AGENT-CREATE-UPGRADE（用户 10-16「之前底座还可以选择图标和颜色」）：
  // 建册可选，空 = 默认渲染。
  const [createAgentIcon, setCreateAgentIcon] = useState('')
  const [createAgentColor, setCreateAgentColor] = useState('')

  const activeSidebarSessionId =
    currentView === 'chat' || currentView === 'console' || currentView === 'agent' ? selectedSessionId : null

  // R-012: bucket agents into the four sidebar categories (missing/unknown
  // category folds into butler) and index agent-owned sessions by profile so
  // each agent row can reveal its own thread list inline.
  const agentCategoryGroups = useMemo(() => groupAgentsByCategory(secretaryAgents), [secretaryAgents])

  // 建册对话框能选哪些品类：声明的顺序 + 名册里已经有的那些。只列前者的话，后端新加一类
  // 就得有人记得来这里补一行——课程那类当初就是这么「选不中」的。
  const createAgentCategoryOptions = useMemo(
    () => creatableCategoryOptions(agentCategoryGroups.map(group => group.category)),
    [agentCategoryGroups]
  )

  const agentProfileSet = useMemo(() => agentProfileIdSet(secretaryAgents), [secretaryAgents])

  const sessionsByAgentProfile = useMemo(() => {
    const map = new Map<string, SessionInfo[]>()

    for (const session of sessions) {
      const profile = (session.profile ?? '').trim()

      if (!profile || !agentProfileSet.has(profile) || isL1SessionProfile(profile, agentProfileSet)) {
        continue
      }

      const bucket = map.get(profile)

      if (bucket) {
        bucket.push(session)
      } else {
        map.set(profile, [session])
      }
    }

    return map
  }, [agentProfileSet, sessions])

  const openCreateAgent = useCallback(() => {
    setCreateAgentName('')
    setCreateAgentError(null)
    setCreateAgentBusy(false)
    setCreateAgentCategory(DEFAULT_AGENT_CATEGORY)
    setCreateAgentPaths([])
    setCreateAgentIcon('')
    setCreateAgentColor('')
    setCreateAgentOpen(true)
  }, [])

  // Reuses the existing remote-aware folder picker (and its dialog) — no second
  // folder UI. Cancelling the picker leaves the list exactly as it was, which
  // also means cancelling never submits anything.
  // WP-AGENT-CREATE-UPGRADE：追加进清单（去重），不再覆盖单值。
  const pickCreateAgentFolder = useCallback(async () => {
    const dir = await pickProjectFolder()

    if (dir && !createAgentPaths.includes(dir)) {
      setCreateAgentPaths([...createAgentPaths, dir])
    }
  }, [createAgentPaths])

  const submitCreateAgent = useCallback(async () => {
    const trimmed = createAgentName.trim()

    if (!trimmed || createAgentBusy) {
      return
    }

    // Events agents need a schedule-backed source_event_id; the dialog
    // disables submit for that category, but guard here too.
    if (isCreateAgentCategoryBlocked(createAgentCategory)) {
      return
    }

    const needsPath = categoryNeedsProjectPath(createAgentCategory)
    const paths = needsPath ? createAgentPaths.map(path => path.trim()).filter(Boolean) : []

    // A workspace agent without a folder is not submittable — never POST a
    // half-built agent (the backend would mkdir an empty workspace).
    if (needsPath && paths.length === 0) {
      return
    }

    const body = buildCreateAgentBody(trimmed, createAgentCategory, paths, {
      color: createAgentColor,
      icon: createAgentIcon
    })

    setCreateAgentBusy(true)
    setCreateAgentError(null)

    try {
      await createAgent(body)
      await refreshConsoleAgents()
      setCreateAgentOpen(false)
      setCreateAgentName('')
      setCreateAgentPaths([])
      setCreateAgentIcon('')
      setCreateAgentColor('')
      navigate(agentRoute(body.id))
    } catch (cause) {
      // 后端拒绝创建（重名 / 撞了 Mind 里别人的树）时这句话必须出现在弹窗里，
      // 不许静默（R-013 扩写 2026-10-06）。判据在后端，这里只摊平显示。
      setCreateAgentError(createAgentErrorMessage(cause))
    } finally {
      setCreateAgentBusy(false)
    }
  }, [
    createAgentBusy,
    createAgentCategory,
    createAgentColor,
    createAgentIcon,
    createAgentName,
    createAgentPaths,
    navigate
  ])

  const expanded = contentVisible
  // 收起条上五类图标是固定的（用户 10-31），没有 Agent 的类型也占一个位；
  // 展开的列表仍只显示真有名册的组。
  const stripGroups = useMemo<AgentCategoryGroupData<Agent>[]>(
    () =>
      AGENT_CATEGORY_PREFERENCE.map(
        category => agentCategoryGroups.find(group => group.category === category) ?? { agents: [], category }
      ),
    [agentCategoryGroups]
  )
  const sharedItems = useMemo(
    () => navItems.filter(item => item.id === 'knowledge' || item.id === 'messaging' || item.id === 'diary'),
    [navItems]
  )
  const capabilitiesHref = navItems.find(item => item.id === 'skills')?.route ?? SKILLS_ROUTE
  const agentActions = useMemo(() => {
    const actions = [
      {
        icon: 'extensions',
        id: 'capabilities',
        label: s.nav.skills,
        onClick: () => navigate(capabilitiesHref)
      },
      {
        icon: 'archive',
        id: 'artifacts',
        label: s.nav.artifacts,
        onClick: () => navigate(ARTIFACTS_ROUTE)
      }
    ]

    if (l2Id) {
      actions.push({ icon: 'send', id: 'offload', label: s.nav.offload, onClick: () => setShowOffload(true) })
    }

    return actions
  }, [capabilitiesHref, l2Id, navigate, s.nav.artifacts, s.nav.offload, s.nav.skills])

  const sharedActive = (id: string) =>
    (id === 'knowledge' && currentView === 'knowledge') ||
    (id === 'messaging' && currentView === 'messaging') ||
    (id === 'diary' && currentView === 'diary')

  return (
    <Sidebar
      className={cn(
        'relative h-full min-w-0 overflow-hidden border-t-0 border-b-0 text-foreground transition-none',
        panesFlipped ? 'border-l border-r-0' : 'border-r border-l-0',
        expanded
          ? 'border-(--sidebar-edge-border) bg-(--ui-sidebar-surface-background)'
          : 'border-transparent bg-transparent'
      )}
      collapsible="none"
    >
      {expanded ? (
        <SidebarContent className="min-h-0 gap-0 bg-transparent px-2.5">
          {showOffload && l2Id ? (
            <div className="min-h-0 flex-1 overflow-y-auto pt-2">
              <AgentOffloadPanel agentId={l2Id} onBack={() => setShowOffload(false)} />
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
              <SidebarMenu className="gap-px pt-2">
                <SidebarMenuItem>
                  <SidebarMenuButton
                    aria-label={c.newAgent}
                    className={NAV_BUTTON}
                    onClick={openCreateAgent}
                    tooltip={c.newAgent}
                    type="button"
                  >
                    <Codicon className={NAV_ICON} name="add" />
                    <span className="min-w-0 flex-1 truncate">{c.newAgentLabel}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>

                {l2Id && (
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      className={NAV_BUTTON}
                      onClick={() => navigate(HOME_ROUTE)}
                      tooltip={c.backToSecretary}
                      type="button"
                    >
                      <Codicon className={NAV_ICON} name="arrow-left" />
                      <span className="min-w-0 flex-1 truncate">{c.backToSecretary}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )}

                {sharedItems.map(item => (
                  <SidebarMenuItem key={item.id}>
                    <SidebarMenuButton
                      className={cn(NAV_BUTTON, sharedActive(item.id) && NAV_BUTTON_ACTIVE)}
                      onClick={() => onNavigate(item)}
                      tooltip={s.nav[item.id] ?? item.label}
                      type="button"
                    >
                      <item.icon className={NAV_ICON} />
                      <span className="min-w-0 flex-1 truncate">{s.nav[item.id] ?? item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}

                <div aria-hidden="true" className="my-2 h-px bg-(--ui-stroke-tertiary)" />

                {secretaryAgentsError && (
                  <div className="mb-1 flex items-start gap-2 px-2 pb-1 text-[0.75rem] leading-snug text-(--ui-text-secondary)">
                    <span className="min-w-0 flex-1" title={secretaryAgentsError}>
                      {c.agentsLoadFailed}
                    </span>
                    <Button onClick={() => void refreshConsoleAgents()} size="sm" type="button" variant="ghost">
                      {t.common.retry}
                    </Button>
                  </div>
                )}

                {secretaryAgents.length === 0 ? (
                  <div className="px-2 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
                    {secretaryAgentsLoading ? c.agentsLoading : c.agentsEmptyDesc}
                  </div>
                ) : (
                  agentCategoryGroups.map(group => (
                    <AgentCategoryGroup
                      activeAgentId={l2Id}
                      activeSessionId={activeSidebarSessionId}
                      agents={group.agents}
                      category={group.category}
                      hideSessions
                      icon={<Codicon name={categoryIcon(group.category)} size="0.875rem" />}
                      key={group.category}
                      label={c.agentCategories[group.category] ?? group.category}
                      onArchiveSession={onArchiveSession}
                      onDeleteSession={onDeleteSession}
                      onNavigateAgent={id => navigate(agentRoute(id))}
                      onResumeSession={onResumeSession}
                      onTogglePin={session => pinSession(sessionPinId(session))}
                      sessionsByProfile={sessionsByAgentProfile}
                      workingSessionIdSet={workingSessionIdSet}
                    />
                  ))
                )}
              </SidebarMenu>
            </div>
          )}

          {/* 本 Agent 的两个入口贴在底部、和下面那排图标同一条底边（用户 10-31）。 */}
          {!showOffload && agentActions.length > 0 ? (
            <div className="shrink-0 border-t border-(--ui-stroke-tertiary) px-1 py-1">
              <SidebarMenu className="gap-px">
                {agentActions.map(action => (
                  <SidebarMenuItem key={action.id}>
                    <SidebarMenuButton
                      className={NAV_BUTTON}
                      onClick={action.onClick}
                      tooltip={action.label}
                      type="button"
                    >
                      <Codicon className={NAV_ICON} name={action.icon} />
                      <span className="min-w-0 flex-1 truncate">{action.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </div>
          ) : null}

          <div className="shrink-0 border-t border-(--ui-stroke-tertiary) px-1.5 py-1">
            <StatusbarControls dock inline items={footerItems} />
          </div>
        </SidebarContent>
      ) : (
        <SidebarStrip
          activeAgentId={l2Id}
          agentActions={agentActions}
          footerItems={footerItems}
          groups={stripGroups}
          onCreateAgent={openCreateAgent}
          onNavigate={onNavigate}
          onNavigateAgent={id => navigate(agentRoute(id))}
          sharedItems={sharedItems}
        />
      )}

      <ProjectDialog />
      <Dialog onOpenChange={value => !value && !createAgentBusy && setCreateAgentOpen(false)} open={createAgentOpen}>
        <DialogContent className="max-w-md" onInteractOutside={event => event.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{c.newAgentLabel}</DialogTitle>
            <DialogDescription>{c.newAgentDesc}</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-4"
            onSubmit={event => {
              event.preventDefault()
              void submitCreateAgent()
            }}
          >
            <div className="grid gap-1.5">
              <label className="text-xs font-medium" htmlFor="new-agent-name">
                {t.profiles.nameLabel}
              </label>
              <Input
                autoFocus
                id="new-agent-name"
                onChange={event => setCreateAgentName(event.target.value)}
                value={createAgentName}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="text-xs font-medium" htmlFor="new-agent-category">
                {c.newAgentCategoryLabel}
              </label>
              <Select
                onValueChange={value => setCreateAgentCategory(value as AgentCategory)}
                value={createAgentCategory}
              >
                <SelectTrigger aria-label={c.newAgentCategoryLabel} id="new-agent-category">
                  <SelectValue placeholder={c.newAgentCategoryLabel} />
                </SelectTrigger>
                <SelectContent>
                  {createAgentCategoryOptions.map(cat => (
                    <SelectItem key={cat} value={cat}>
                      {c.agentCategories[cat] ?? cat}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {/* WP-AGENT-CREATE-UPGRADE：每类一行帮助——选这类是干什么的。
                  可选链：i18n 键被别线的整文件覆写卷走时降级为空串，绝不打挂整棵树。 */}
              <p className="text-[0.6875rem] text-(--ui-text-tertiary)">
                {c.newAgentCategoryHelp?.[createAgentCategory] ?? ''}
              </p>
            </div>
            {/* WP-STUDIO (裁定 36.1): a projects/research/courses agent needs the
                folder(s) it works in. Butler agents work nowhere in particular,
                so the picker never appears for them.
                WP-AGENT-CREATE-UPGRADE（用户 10-16「一个 Agent 肯定也要可以添加
                多个文件夹」）：单值 → 清单，可加多个；第一个是主文件夹。 */}
            {categoryNeedsProjectPath(createAgentCategory) && (
              <div className="grid gap-1.5">
                <label className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">
                  {c.newAgentFoldersLabel}
                </label>
                {createAgentPaths.length === 0 ? (
                  <p className="text-[0.75rem] text-(--ui-text-tertiary)">
                    {c.newAgentFolderPlaceholder}
                  </p>
                ) : (
                  <ul className="grid gap-1">
                    {createAgentPaths.map((path, index) => (
                      <li
                        className={cn(
                          'flex items-center gap-2 rounded-md bg-(--ui-control-hover-background) px-2 py-1 text-[0.75rem]'
                        )}
                        key={path}
                      >
                        <Codicon
                          className="shrink-0 text-(--ui-text-tertiary)"
                          name="folder"
                          size="0.75rem"
                        />
                        <span className="min-w-0 flex-1 truncate" title={path}>
                          {path}
                        </span>
                        {index === 0 && (
                          <span className="shrink-0 text-[0.625rem] uppercase text-(--ui-text-quaternary)">
                            {c.newAgentFolderPrimary}
                          </span>
                        )}
                        <Button
                          aria-label={c.newAgentFolderRemove}
                          className="size-5 shrink-0 text-(--ui-text-quaternary) hover:text-foreground"
                          onClick={() =>
                            setCreateAgentPaths(createAgentPaths.filter(item => item !== path))
                          }
                          size="icon-xs"
                          type="button"
                          variant="ghost"
                        >
                          <Codicon name="close" size="0.75rem" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                <Button
                  className="self-start"
                  onClick={() => void pickCreateAgentFolder()}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  <Codicon name="add" size="0.75rem" />
                  {c.newAgentFolderAdd}
                </Button>
                <p className="text-[0.6875rem] text-(--ui-text-tertiary)">
                  {c.newAgentFolderPrimaryHint}
                </p>
              </div>
            )}
            {/* WP-AGENT-CREATE-UPGRADE：图标与颜色（用户 10-16 要回的「可爱」）。 */}
            <div className="flex flex-col gap-1.5">
              <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">
                {c.newAgentAppearanceLabel}
              </span>
              <div className="flex flex-wrap items-center gap-1">
                {AGENT_ICON_CHOICES.map(name => (
                  <button
                    aria-label={name}
                    aria-pressed={createAgentIcon === name}
                    className={cn(
                      'flex size-7 items-center justify-center rounded-md',
                      createAgentIcon === name
                        ? 'bg-(--ui-control-hover-background) text-foreground'
                        : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground'
                    )}
                    key={name}
                    onClick={() => setCreateAgentIcon(createAgentIcon === name ? '' : name)}
                    type="button"
                  >
                    <Codicon name={name} size="0.875rem" />
                  </button>
                ))}
                <Button
                  aria-label={c.newAgentAppearanceClear}
                  className="size-7 text-(--ui-text-quaternary) hover:text-foreground"
                  onClick={() => {
                    setCreateAgentIcon('')
                    setCreateAgentColor('')
                  }}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <Codicon name="close" size="0.75rem" />
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {AGENT_COLOR_CHOICES.map(color => (
                  <button
                    aria-label={color}
                    aria-pressed={createAgentColor === color}
                    className={cn(
                      'size-5 rounded-full ring-offset-2 transition-shadow',
                      createAgentColor === color
                        ? 'ring-2 ring-(--ui-text-tertiary) ring-offset-(--ui-background)'
                        : 'hover:ring-2 hover:ring-(--ui-text-quaternary) hover:ring-offset-(--ui-background)'
                    )}
                    key={color}
                    onClick={() => setCreateAgentColor(createAgentColor === color ? '' : color)}
                    style={{ backgroundColor: color }}
                    type="button"
                  />
                ))}
              </div>
              <p className="text-[0.6875rem] text-(--ui-text-tertiary)">
                {c.newAgentAppearanceHint}
              </p>
            </div>
            {isCreateAgentCategoryBlocked(createAgentCategory) && (
              <p className="text-xs text-destructive">{c.createEventAgentHint}</p>
            )}
            {createAgentError && <p className="text-xs text-destructive">{createAgentError}</p>}
            <DialogFooter>
              <Button disabled={createAgentBusy} onClick={() => setCreateAgentOpen(false)} type="button" variant="ghost">
                {t.common.cancel}
              </Button>
              <Button
                disabled={
                  createAgentBusy ||
                  isCreateAgentSubmitDisabled(createAgentName, createAgentCategory, createAgentPaths)
                }
                type="submit"
              >
                {c.newAgentLabel}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Sidebar>
  )
}
