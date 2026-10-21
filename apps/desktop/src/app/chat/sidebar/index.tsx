import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { SidebarPanelLabel } from '@/app/shell/sidebar-label'
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
import { $panesFlipped, $sidebarOpen, $sidebarOverlayMounted, pinSession } from '@/store/layout'
import { $activeGatewayProfile } from '@/store/profile'
import { pickProjectFolder } from '@/store/projects'
import { $selectedStoredSessionId, $sessions, $workingSessionIds, sessionPinId } from '@/store/session'

import { createAgent } from '../../console/api'
import { $consoleAgents, $consoleAgentsLoading, refreshConsoleAgents } from '../../console/store/agents'
import { secretaryShellLabel } from '../../desktop-controller-utils'
import {
  agentRoute,
  type AppView,
  ARTIFACTS_ROUTE,
  HOME_ROUTE,
  MESSAGING_ROUTE,
  type ShellLevel,
  SKILLS_ROUTE
} from '../../routes'
import type { SidebarNavItem } from '../../types'

import { AgentCategoryGroup } from './agent-category-group'
import { agentProfileIdSet, groupAgentsByCategory, isL1SessionProfile, normalizeAgentCategory } from './agent-groups'
import { AgentOffloadPanel } from './agent-offload-panel'
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
import { hidesAgentSessions, sidebarNavForLevel } from './nav-level'
import { ProjectDialog } from './project-dialog'

const SIDEBAR_NAV: SidebarNavItem[] = [
  {
    id: 'skills',
    label: '',
    icon: props => <Codicon name="symbol-misc" {...props} />,
    route: SKILLS_ROUTE
  },
  { id: 'messaging', label: '', icon: props => <Codicon name="comment" {...props} />, route: MESSAGING_ROUTE },
  { id: 'artifacts', label: '', icon: props => <Codicon name="files" {...props} />, route: ARTIFACTS_ROUTE }
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
  currentView: AppView
  level: ShellLevel | null
  onNavigate: (item: SidebarNavItem) => void
  onResumeSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => void
  onArchiveSession: (sessionId: string) => void
}

export function ChatSidebar({
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
  // 点进一个 L2 时左栏这一格默认换成"外包给了谁"；这一格只记住"我按了返回"，
  // 换一个分身就回到默认（不是全局开关，也不改导航）。
  const [offloadsBackToList, setOffloadsBackToList] = useState(false)

  useEffect(() => {
    setOffloadsBackToList(false)
  }, [l2Id])
  const secretaryAgentsLoading = useStore($consoleAgentsLoading)

  // 左栏现在只有 Agents 一张列表（裁定 38/39 终态：会话/profile 不再是产品
  // 概念），overlay 页也常驻 → 名册在挂载时拉一次。
  useEffect(() => {
    void refreshConsoleAgents()
  }, [])

  // 左栏那颗 Capabilities 指向**当前 Agent 自己的**那一页（2026-10-20 用户裁定 C）。
  // L2 → 它名册行的 profile；L1 也是 Agent，用当前生效的 profile。解析不到 profile
  // 就退回不带参数的 /skills（那是全局真源，只在「设置 → Capabilities」里用）。
  const liveProfile = useStore($activeGatewayProfile)

  const navItems = useMemo(() => {
    const base = sidebarNavForLevel(SIDEBAR_NAV, { l1, l2Id })

    const activeProfile =
      (l2Id ? secretaryAgents.find(agent => agent.id === l2Id)?.profile : null) || liveProfile || null

    return base.map(item =>
      item.id === 'skills' && activeProfile
        ? { ...item, route: `${SKILLS_ROUTE}?profile=${encodeURIComponent(activeProfile)}` }
        : item
    )
  }, [l1, l2Id, liveProfile, secretaryAgents])

  const sidebarOpen = useStore($sidebarOpen)
  // Collapsed-but-overlay-mounted → render the full sidebar, not just the nav rail.
  const overlayMounted = useStore($sidebarOverlayMounted)
  const contentVisible = sidebarOpen || overlayMounted
  const panesFlipped = useStore($panesFlipped)
  const selectedSessionId = useStore($selectedStoredSessionId)
  const sessions = useStore($sessions)
  const workingSessionIds = useStore($workingSessionIds)

  const workingSessionIdSet = useMemo(() => new Set(workingSessionIds), [workingSessionIds])

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

  // R-012: the active agent's category badge for the /agent/:id shell label.
  const l2CategoryLabel = useMemo(() => {
    if (!l2Id) {
      return ''
    }

    const agent = secretaryAgents.find(row => row.id === l2Id)

    return agent ? (c.agentCategories[normalizeAgentCategory(agent.category)] ?? '') : ''
  }, [c.agentCategories, l2Id, secretaryAgents])

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

  return (
    <Sidebar
      className={cn(
        'relative h-full min-w-0 overflow-hidden border-t-0 border-b-0 text-foreground transition-none',
        panesFlipped ? 'border-l border-r-0' : 'border-r border-l-0',
        sidebarOpen
          ? 'border-(--sidebar-edge-border) bg-(--ui-sidebar-surface-background) opacity-100'
          : 'pointer-events-none border-transparent bg-transparent opacity-0',
        // While floated by PaneShell's hover-reveal, force visible + interactive
        // — on hover (group-hover/reveal) or when keyboard-pinned (data-forced).
        'in-data-[pane-hover-reveal=open]:pointer-events-auto in-data-[pane-hover-reveal=open]:border-(--sidebar-edge-border) in-data-[pane-hover-reveal=open]:bg-(--ui-sidebar-surface-background) in-data-[pane-hover-reveal=open]:opacity-100',
        'group-hover/reveal:pointer-events-auto group-hover/reveal:border-(--sidebar-edge-border) group-hover/reveal:bg-(--ui-sidebar-surface-background) group-hover/reveal:opacity-100'
      )}
      collapsible="none"
    >
      <SidebarContent className="gap-0 overflow-hidden bg-transparent px-2.5">
        <SidebarGroup className="shrink-0 p-0 pb-2 pt-[calc(var(--titlebar-height)+0.375rem)]">
          <SidebarGroupContent>
            <SidebarMenu className="gap-px">
              {level && contentVisible && (
                <div className="flex items-center gap-2 pb-1.5 pt-0.5">
                  <SidebarPanelLabel>{secretaryShellLabel(level, c.chiefSecretary, secretaryAgents)}</SidebarPanelLabel>
                  {/* R-012: category badge for the active agent's workbench. */}
                  {l2CategoryLabel && (
                    <span className="shrink-0 rounded border border-(--ui-stroke-tertiary) px-1.5 py-0.5 text-[0.6rem] font-medium leading-none text-(--ui-text-tertiary)">
                      {l2CategoryLabel}
                    </span>
                  )}
                </div>
              )}
              {!l2Id && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    aria-label={c.newAgent}
                    className={NAV_BUTTON}
                    onClick={openCreateAgent}
                    tooltip={c.newAgent}
                    type="button"
                  >
                    <Codicon className={NAV_ICON} name="add" />
                    {contentVisible && <span className="min-w-0 flex-1 truncate">{c.newAgentLabel}</span>}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {l2Id && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    className={NAV_BUTTON}
                    onClick={() => navigate(HOME_ROUTE)}
                    tooltip={c.backToSecretary}
                    type="button"
                  >
                    <Codicon className={NAV_ICON} name="arrow-left" />
                    {contentVisible && <span className="min-w-0 flex-1 truncate">{c.backToSecretary}</span>}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {navItems.map(item => {
                const isInteractive = Boolean(item.action) || Boolean(item.route)

                const active =
                  (item.id === 'skills' && currentView === 'skills') ||
                  (item.id === 'messaging' && currentView === 'messaging') ||
                  (item.id === 'artifacts' && currentView === 'artifacts')

                return (
                  <SidebarMenuItem key={item.id}>
                    <SidebarMenuButton
                      aria-disabled={!isInteractive}
                      className={cn(NAV_BUTTON, active && NAV_BUTTON_ACTIVE, !isInteractive && NAV_BUTTON_DISABLED)}
                      onClick={() => onNavigate(item)}
                      tooltip={s.nav[item.id] ?? item.label}
                      type="button"
                    >
                      <item.icon className={NAV_ICON} />
                      {contentVisible && (
                        <span className="min-w-0 flex-1 truncate">{s.nav[item.id] ?? item.label}</span>
                      )}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* 纯 Agents 列表：每个壳（L1 / L2 / overlay 页）都常驻 —— 裁定 38/39
            终态，会话与 profile 段已整体撤出左栏。 */}
        <SidebarGroup className="shrink-0 p-0 pb-1">
          <SidebarGroupContent>
            {l2Id && !offloadsBackToList && (
              <AgentOffloadPanel agentId={l2Id} onBack={() => setOffloadsBackToList(true)} />
            )}
            {(!l2Id || offloadsBackToList) && (<>
              {/* Match PINNED/PROJECTS: SidebarPanelLabel already has pl-2 + dot — no extra px-2. */}
              <div className="flex shrink-0 items-center pb-1 pt-0.5">
                <SidebarPanelLabel>{c.agentsTitle}</SidebarPanelLabel>
              </div>
              {secretaryAgents.length === 0 ? (
                <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
                  {secretaryAgentsLoading ? c.agentsLoading : c.agentsEmptyDesc}
                </div>
              ) : (
                <SidebarMenu className="gap-px">
                  {agentCategoryGroups.map(group => (
                    <AgentCategoryGroup
                      activeAgentId={l2Id}
                      activeSessionId={activeSidebarSessionId}
                      agents={group.agents}
                      category={group.category}
                      hideSessions={hidesAgentSessions(level)}
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
                  ))}
                </SidebarMenu>
              )}
            </>)}
          </SidebarGroupContent>
        </SidebarGroup>

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
      </SidebarContent>
    </Sidebar>
  )
}

