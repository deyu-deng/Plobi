import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { beginEntering, endEntering, enterFailure } from '@/app/chat/agent-enter'
import { $agentEnter, settleAgentEnter } from '@/app/chat/agent-enter-store'
import { BootFailureOverlay } from '@/components/boot-failure-overlay'
import { DesktopInstallOverlay } from '@/components/desktop-install-overlay'
import { GatewayConnectingOverlay } from '@/components/gateway-connecting-overlay'
import { DesktopOnboardingOverlay } from '@/components/onboarding'
import { PageLoader } from '@/components/page-loader'
import { Pane, PaneMain } from '@/components/pane-shell'
import { RemoteDisplayBanner } from '@/components/remote-display-banner'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useI18n } from '@/i18n'
import { isFocusWithin } from '@/lib/keybinds/combo'
import { mediaKind } from '@/lib/media'
import { readKey, storageKey, writeKey } from '@/lib/storage'
import { cn } from '@/lib/utils'
import { $agendaError, $agendaEvents, $agendaLoading } from '@/store/agenda'
import { useSkinCommand } from '@/themes/use-skin-command'
import type { AgendaEvent } from '@/types/plobi'

import { storedSessionIdForNotification } from '../lib/session-ids'
import { setAgentScope } from '../store/agent-scope'
import type { ComposerAttachment } from '../store/composer'
import {
  $fileBrowserOpen,
  $panesFlipped,
  $rightRailView,
  $shellCards,
  $shellSwapped,
  $sidebarOpen,
  $cardFocus,
  setCardFocus,
  setShellSwapped,
  FILE_BROWSER_DEFAULT_WIDTH,
  FILE_BROWSER_MIN_WIDTH,
  PREVIEW_PANE_ID,
  RIGHT_RAIL_VIEW_NOTES,
  setFileBrowserOpen,
  SIDEBAR_COLLAPSED_WIDTH,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  toggleShellSwapped
} from '../store/layout'
import { cardDragProps, useCardDrop } from './shell/card-drag'
import { useSystemTools } from './shell/system-tools'
import { respondToApprovalAction } from '../store/native-notifications'
import { $paneOpen, setPaneOpen, togglePane } from '../store/panes'
import { $filePreviewTarget, $previewTarget, closeActiveRightRailTab, type PreviewTarget } from '../store/preview'
import { $dirtyPreviewUrls } from '../store/preview-edit'
import { $activeGatewayProfile, $profiles, $profileScope, ensureGatewayProfile, normalizeProfileKey, refreshProfiles } from '../store/profile'
import { $reviewOpen, closeReview, openReview } from '../store/review'
import {
  $activeSessionId,
  $agentCwd,
  $currentCwd,
  $freshDraftReady,
  $gatewayState,
  $messagingSessions,
  $resumeExhaustedSessionId,
  $resumeFailedSessionId,
  $selectedStoredSessionId,
  $sessions,
  getRememberedSessionId,
  releaseAgentCwd,
  setAgentCwd,
  setAwaitingResponse,
  setBusy,
  setCurrentModel,
  setCurrentProvider,
  setMessages,
  setRememberedSessionId
} from '../store/session'
import { onSessionsChanged } from '../store/session-sync'
import { openUpdatesWindow, startUpdatePoller, stopUpdatePoller } from '../store/updates'
import { isSecondaryWindow } from '../store/windows'

import { fileUrl } from './agenda/detail'
import { requestComposerFocus, requestComposerInsert } from './chat/composer/focus'
import { useComposerActions } from './chat/hooks/use-composer-actions'
import {
  ChatPreviewRail,
  PREVIEW_RAIL_MIN_WIDTH,
  PREVIEW_RAIL_PANE_WIDTH
} from './chat/right-rail'
import { ChatSidebar } from './chat/sidebar'
import { CommandPalette } from './command-palette'
import { getAgentOverview } from './console/api'
import { agentScope, L1_SCOPE } from './console/chat/scope'
import { AddEventForm } from './console/right/add-event'
import { RailEventDetail } from './console/right/event-detail'
import { Timeline } from './console/right/timeline'
import {
  $agendaAvoidWindows,
  $agendaRhythmOff,
  refreshAgendaTimeline,
  useAgendaTimeline
} from './console/right/use-agenda-timeline'
import { $consoleAgents, refreshConsoleAgents } from './console/store/agents'
import {
  bindL1GatewayIfMasterExists,
  findKnownOverviewSession,
  pickL1Mouth,
  readL1MainSessionId,
  resolveL1HomeProfile,
  secretaryShellLabel,
  writeL1MainSessionId
} from './desktop-controller-utils'
import { LegacySessionRedirect, SecretaryCenterPane } from './desktop-views'
import { useGatewayBoot } from './gateway/hooks/use-gateway-boot'
import { useGatewayRequest } from './gateway/hooks/use-gateway-request'
import { useProfileScopeSync } from './gateway/hooks/use-profile-scope-sync'
import { useBackgroundPolling } from './hooks/use-background-polling'
import { useKeybinds } from './hooks/use-keybinds'
import { usePetBridge } from './hooks/use-pet-bridge'
import { SIDEBAR_COLLAPSE_MEDIA_QUERY } from './layout-constants'
import { ModelPickerOverlay } from './model-picker-overlay'
import { ModelVisibilityOverlay } from './model-visibility-overlay'
import { PetGenerateOverlay } from './pet-generate/pet-generate-overlay'
import { RightSidebarPane } from './right-sidebar'
import { FileActionDialogs } from './right-sidebar/file-actions'
import { RemoteFolderPicker } from './right-sidebar/files/remote-picker'
import { ReviewPane } from './right-sidebar/review'
import { $terminalTakeover, setTerminalTakeover } from './right-sidebar/store'
import { TerminalPaneChrome } from './right-sidebar/terminal/chrome'
import { PersistentTerminal } from './right-sidebar/terminal/persistent'
import { closeActiveTerminal } from './right-sidebar/terminal/terminals'
import {
  HOME_ROUTE,
  isShellPageView,
  NEW_CHAT_ROUTE,
  routeSessionId,
  sessionRoute,
  SETTINGS_ROUTE,
  shellLevelForPath
} from './routes'
import { BrowserTabPanel } from './workbench/browser-tab'
import { LocalFilePreview } from './chat/right-rail/preview-file'
import { fileKindForPath } from './workbench/file-kind'
import { isModelPath } from './workbench/model-paths'
import { queryGenOfficeDirty } from './workbench/genoffice-dirty'
import { requestGenOfficeClose } from './workbench/genoffice-sessions'
import { DiaryCaptureOverlay } from './diary/capture-overlay'
import { RightRailWorkbench, type RightRailTab, type RightRailTabDef, type RightRailTabId } from './workbench/right-rail'
import { AgentProgressPane } from './chat/right-rail/agent-progress-pane'
import { SessionPickerOverlay } from './session-picker-overlay'
import { SessionSwitcher } from './session-switcher'
import { useContextSuggestions } from './session/hooks/use-context-suggestions'
import { useConversationReset } from './session/hooks/use-conversation-reset'
import { useCwdActions } from './session/hooks/use-cwd-actions'
import { useMessageErrorDismiss } from './session/hooks/use-message-error-dismiss'
import { useMessageStream } from './session/hooks/use-message-stream'
import { useModelControls } from './session/hooks/use-model-controls'
import { usePlobiConfig } from './session/hooks/use-plobi-config'
import { usePreviewRouting } from './session/hooks/use-preview-routing'
import { usePromptActions } from './session/hooks/use-prompt-actions'
import { useRouteResume } from './session/hooks/use-route-resume'
import { useSessionActions } from './session/hooks/use-session-actions'
import { useSessionListActions } from './session/hooks/use-session-list-actions'
import { useSessionPinToggle } from './session/hooks/use-session-pin-toggle'
import { useSessionStateCache } from './session/hooks/use-session-state-cache'
import { useTranscriptRehydration } from './session/hooks/use-transcript-rehydration'
import { useWorkspaceSessionStart } from './session/hooks/use-workspace-session-start'
import { AppShell } from './shell/app-shell'
import { useOverlayRouting } from './shell/hooks/use-overlay-routing'
import { useStatusSnapshot } from './shell/hooks/use-status-snapshot'
import { useStatusbarItems } from './shell/hooks/use-statusbar-items'
import { ModelMenuPanel } from './shell/model-menu-panel'
import type { StatusbarItem } from './shell/statusbar-controls'
import type { TitlebarTool } from './shell/titlebar-controls'
import { useGroupRegistry } from './shell/use-group-registry'
import { MindPendingOverlay } from '@/app/mind-pending-overlay'
import { UpdatesOverlay } from './updates-overlay'

const AgendaView = lazy(async () => ({ default: (await import('./agenda')).AgendaView }))
const AgentsView = lazy(async () => ({ default: (await import('./agents')).AgentsView }))
const ArtifactsView = lazy(async () => ({ default: (await import('./artifacts')).ArtifactsView }))
const CommandCenterView = lazy(async () => ({ default: (await import('./command-center')).CommandCenterView }))
const CronView = lazy(async () => ({ default: (await import('./cron')).CronView }))
const StarmapView = lazy(async () => ({ default: (await import('./starmap')).StarmapView }))
const MessagingView = lazy(async () => ({ default: (await import('./messaging')).MessagingView }))
const SettingsView = lazy(async () => ({ default: (await import('./settings')).SettingsView }))
const OfficeFilePreview = lazy(async () => ({
  default: (await import('./workbench/office-preview')).OfficeFilePreview
}))
const MediaPreview = lazy(async () => ({
  default: (await import('./workbench/media-preview')).MediaPreview
}))
const ArchivePreview = lazy(async () => ({
  default: (await import('./workbench/archive-preview')).ArchivePreview
}))
const ThreePreview = lazy(async () => ({
  default: (await import('./workbench/three-preview')).ThreePreview
}))
const SkillsView = lazy(async () => ({ default: (await import('./skills')).SkillsView }))
const KnowledgeView = lazy(async () => ({ default: (await import('./knowledge')).KnowledgeView }))
const DiaryView = lazy(async () => ({ default: (await import('./diary')).DiaryView }))

// ── 文件页签按工作区记住 ──────────────────────────────────────────────────
// 用户 10-31：在 A 这个 Agent 里开着页签，切去 B 说句话再回来，页签全没了。
// 页签本来就属于 Agent 的文件夹，切走时该收起来而不是丢掉——所以按 cwd 分格存，
// 内存一份（同一次运行内立刻可用）+ localStorage 一份（重启后还在）。
// 文件被删/移走时不报错：那一条恢复失败就跳过，界面照常。
type OpenFileTabs = Record<string, { def: RightRailTabDef; target: PreviewTarget }>

const FILE_TABS_STORAGE = 'desktop.workbench.fileTabs'

function fileTabsKey(workspace: string): string {
  return `${storageKey(FILE_TABS_STORAGE)}.${workspace || 'l1'}`
}

function persistFileTabs(workspace: string, files: OpenFileTabs) {
  try {
    const rows = Object.entries(files).map(([id, entry]) => ({
      id,
      target: entry.target,
      title: entry.def.title
    }))

    writeKey(fileTabsKey(workspace), JSON.stringify(rows))
  } catch {
    // 存不下就算了：页签只是便利，不该让一次写失败冒到界面上。
  }
}

function loadFileTabs(workspace: string): OpenFileTabs {
  try {
    const raw = readKey(fileTabsKey(workspace))

    if (!raw) {
      return {}
    }

    const rows = JSON.parse(raw) as Array<{ id?: string; target?: PreviewTarget; title?: string }>
    const restored: OpenFileTabs = {}

    for (const row of rows) {
      if (!row?.id || !row.target || !row.id.startsWith('file:')) {
        continue
      }

      restored[row.id] = {
        def: { icon: 'file', id: row.id as RightRailTabId, title: row.title ?? row.id },
        target: row.target
      }
    }

    return restored
  } catch {
    return {}
  }
}

export function DesktopController() {
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useI18n()

  const busyRef = useRef(false)
  const creatingSessionRef = useRef(false)

  const gatewayState = useStore($gatewayState)
  const activeSessionId = useStore($activeSessionId)
  const currentCwd = useStore($currentCwd)
  const agentCwd = useStore($agentCwd)
  const freshDraftReady = useStore($freshDraftReady)
  const resumeFailedSessionId = useStore($resumeFailedSessionId)
  const resumeExhaustedSessionId = useStore($resumeExhaustedSessionId)
  const filePreviewTarget = useStore($filePreviewTarget)
  const previewTarget = useStore($previewTarget)
  const rightRailView = useStore($rightRailView)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const messagingSessions = useStore($messagingSessions)
  const terminalTakeover = useStore($terminalTakeover)
  const reviewOpen = useStore($reviewOpen)
  const fileBrowserOpen = useStore($fileBrowserOpen)
  const dirtyPreviewUrls = useStore($dirtyPreviewUrls)
  const previewPaneOpen = useStore($paneOpen(PREVIEW_PANE_ID))
  const agendaPaneOpen = useStore($paneOpen('agenda-timeline'))
  const panesFlipped = useStore($panesFlipped)
  const shellCards = useStore($shellCards)
  const sidebarOpen = useStore($sidebarOpen)
  // 对话卡 ↔ 右栏卡互换（用户 10-31 选的换法）：交换后右栏当主区、对话进右侧窄卡。
  const swapped = useStore($shellSwapped)
  const cardFocus = useStore($cardFocus)
  const focusWorkbench = cardFocus === 'workbench'
  const focusChat = cardFocus === 'chat'
  const chatDrop = useCardDrop('chat', toggleShellSwapped)
  const railDrop = useCardDrop('rail', toggleShellSwapped)
  const profileScope = useStore($profileScope)
  // WP-D: the L1 agenda rail reads the same single agenda store the full-screen
  // board renders; the controller subscribes so the base Pane can host it.
  const agendaEvents = useStore($agendaEvents)
  const agendaAvoidWindows = useStore($agendaAvoidWindows)
  const agendaRhythmOff = useStore($agendaRhythmOff)
  const agendaLoading = useStore($agendaLoading)
  const agendaError = useStore($agendaError)
  const consoleAgents = useStore($consoleAgents)
  // Below SIDEBAR_COLLAPSE_BREAKPOINT_PX there's no room for a docked rail —
  // collapse both sidebars (without touching their stored open state) so the
  // hover-reveal overlay becomes the way in. Restores once it's wide again.
  const narrowViewport = useMediaQuery(SIDEBAR_COLLAPSE_MEDIA_QUERY)

  const routedSessionId = routeSessionId(location.pathname)
  // WP-A: the secretary identity of the current route, a pure function of the
  // pathname (L1 landing `/` ↔ a specific L2 `/agent/:id`). Only the left/right
  // rail CONTENT branches on it — the shell and center column don't.
  const level = useMemo(() => shellLevelForPath(location.pathname), [location.pathname])
  const routeToken = `${location.pathname}:${location.search}:${location.hash}`
  const routeTokenRef = useRef(routeToken)
  routeTokenRef.current = routeToken
  const getRouteToken = useCallback(() => routeTokenRef.current, [])
  // Last non-agent profile L1 successfully bound to. Returning from L2 without
  // `master` must restore this — otherwise "stayed" keeps the agent profile and
  // the center resumes the L2 transcript under L1 chrome.
  const l1GatewayProfileRef = useRef(normalizeProfileKey($activeGatewayProfile.get() || 'default'))

  const {
    agentsOpen,
    agendaOpen,
    chatOpen,
    closeOverlayToPreviousRoute,
    commandCenterInitialSection,
    commandCenterOpen,
    cronOpen,
    currentView,
    openAgents,
    openCommandCenterSection,
    openStarmap,
    settingsOpen,
    starmapOpen,
    toggleCommandCenter
  } = useOverlayRouting()

  // WP-A: L1/L2 and the legacy chat share one three-pane shell. `chatOpen` only
  // covered `/new` + `/:sessionId`; widening it to the secretary routes keeps
  // the sidebar visible there too. Skills/messaging/artifacts are main-pane
  // pages (not overlays) — they keep the left rail so you can click out.
  const conversationShellOpen = chatOpen || level !== null
  const sidebarPaneOpen = conversationShellOpen || isShellPageView(currentView)

  // The secretary center's scope is a pure function of `level`. Memoized so the
  // center `ChatSurface` re-keys its store without remounting across L1 ↔ L2
  // navigation (WP-A acceptance: center + shell must not remount).
  const chatScope = useMemo(() => {
    if (level === 'l1') {
      return L1_SCOPE
    }

    return level ? agentScope(level.l2) : null
  }, [level])

  // WP-D: §5.3 right-column allocation — the agenda timeline is the L1
  // secretary rail (L2 has a workspace boundary instead); the file tree and
  // terminal belong to the L2 workspace. One branch per rail slot, no overlap.
  const isL2 = level !== 'l1' && level !== null
  // Preview rail must follow the secretary shell (real center ChatSurface),
  // never legacy `chatOpen` (`/:sessionId` / `/new`). Those paths briefly
  // reported chat while the center was only a Navigate→L1, so a leftover
  // markdown tab stole the full main column (flash / stuck preview bug).
  const secretaryShellOpen = level !== null
  const activeAgentId = level && level !== 'l1' ? level.l2 : ''

  // 终端页签按 Agent 分家（10-31）：把当前路由的 Agent 身份写进 store。
  // 归属键只认这个值，不认会话 id —— 一个 Agent 一张嘴、一套页签；同一个
  // Agent 的多轮对话共用同一套。L1 / 裸聊归一成 'l1'。
  useEffect(() => {
    setAgentScope(activeAgentId || 'l1')
  }, [activeAgentId])

  const previewRailContent =
    Boolean(previewTarget) ||
    (isL2 && Boolean(filePreviewTarget)) ||
    // The notes face has no file behind it, so it has to keep the rail mounted by
    // itself — but only on an agent route, where "this agent's notes" means something.
    (isL2 && rightRailView === RIGHT_RAIL_VIEW_NOTES)

  // The L1 rail owns the agenda poll; every other route leaves the store cold
  // (`enabled: false` → no seed, no 8s interval).
  useAgendaTimeline(level !== null)
  const [agendaAdding, setAgendaAdding] = useState(false)
  // 点轴上的块 → 右栏就地换成这条日程的详情（用户 10-31）。null ＝ 还在时间轴。
  // 存整条事件而不是 id：选中别的日子时那一行不在共享 store 里。
  const [railEvent, setRailEvent] = useState<AgendaEvent | null>(null)
  const [l1Tabs, setL1Tabs] = useState<RightRailTabId[]>([])
  const [l1Active, setL1Active] = useState<null | RightRailTabId>(null)
  const [l2Tabs, setL2Tabs] = useState<RightRailTabId[]>([])
  const [l2Active, setL2Active] = useState<null | RightRailTabId>(null)
  const [openFiles, setOpenFiles] = useState<
    Record<string, { def: RightRailTabDef; target: PreviewTarget }>
  >({})
  const [dirtyCloseTab, setDirtyCloseTab] = useState<null | RightRailTabId>(null)
  const closingFileTabs = useRef(new Set<RightRailTabId>())

  const workbenchTabDefs: Record<RightRailTab, RightRailTabDef> = {
    agenda: {
      description: t.workbench.actions.agenda,
      icon: 'calendar',
      id: 'agenda',
      title: t.workbench.tabs.agenda
    },
    browser: {
      description: t.workbench.actions.browser,
      icon: 'globe',
      id: 'browser',
      title: t.workbench.tabs.browser
    },
    files: {
      description: t.workbench.actions.files,
      icon: 'folder-opened',
      id: 'files',
      title: t.workbench.tabs.files
    },
    review: {
      description: t.workbench.actions.review,
      icon: 'git-pull-request',
      id: 'review',
      title: t.workbench.tabs.review
    },
    terminal: {
      description: t.workbench.actions.terminal,
      icon: 'terminal',
      id: 'terminal',
      title: t.workbench.tabs.terminal
    },
    progress: {
      description: t.workbench.actions.progress,
      icon: 'graph',
      id: 'progress',
      title: t.workbench.tabs.progress
    }
  }
  const l1TabDefs = [workbenchTabDefs.agenda, workbenchTabDefs.browser, workbenchTabDefs.terminal]
  const l2TabDefs = [
    workbenchTabDefs.files,
    workbenchTabDefs.agenda,
    workbenchTabDefs.review,
    workbenchTabDefs.browser,
    workbenchTabDefs.terminal,
    workbenchTabDefs.progress
  ]
  const isL1Workbench = level === 'l1'

  // 文件页签两条腿共用一张 `openFiles` 表，但各自的标签栏是分开的（L1 是
  // agendaWorkbench，L2 是 filesWorkbench）。所以「开一个文件」要按当前腿写进
  // 对应的 tab 列表，否则页签会落到另一条腿的栏里、或者干脆不显示。
  const openFileTab = useCallback(
    (target: PreviewTarget) => {
      const key = target.path ?? target.url ?? 'preview'
      const title = key.split(/[\\/]/).filter(Boolean).pop() ?? key
      const id = `file:${key}` as RightRailTabId

      setOpenFiles(files => ({ ...files, [id]: { def: { icon: 'file', id, title }, target } }))

      if (isL1Workbench) {
        setL1Tabs(tabs => (tabs.includes(id) ? tabs : [...tabs, id]))
        setL1Active(id)
        setPaneOpen('agenda-timeline', true)

        return
      }

      setL2Tabs(tabs => (tabs.includes(id) ? tabs : [...tabs, id]))
      setL2Active(id)
      setFileBrowserOpen(true)
    },
    [isL1Workbench]
  )

  // 附件点了在当前右栏开一个页签看内容（用户 10-31）。L1 的标签栏现在也收文件
  // 页签了（`openFileTab` 按腿分流），所以两条腿走同一条路。
  const openAgendaAttachment = useCallback(
    (attachment: { name: string; path: string }) => {
      openFileTab({
        kind: 'file',
        label: attachment.name,
        path: attachment.path,
        source: attachment.path,
        url: fileUrl(attachment.path)
      })
    },
    [openFileTab]
  )
  const handleWorkbenchTabChange = useCallback(
    (tab: RightRailTabId) => {
      if (isL1Workbench) {
        setL1Active(tab)
      } else {
        setL2Active(tab)
      }

      if (tab === 'review') {
        openReview()
      }

      if (tab === 'terminal') {
        setTerminalTakeover(true)
      }
    },
    [isL1Workbench]
  )

  const handleWorkbenchTabOpen = useCallback(
    (tab: RightRailTabId) => {
      if (isL1Workbench) {
        setL1Tabs(tabs => (tabs.includes(tab) ? tabs : [...tabs, tab]))
        setL1Active(tab)
        setPaneOpen('agenda-timeline', true)
      } else {
        setL2Tabs(tabs => (tabs.includes(tab) ? tabs : [...tabs, tab]))
        setL2Active(tab)
        setFileBrowserOpen(true)
      }

      if (tab === 'review') {
        openReview()
      }

      if (tab === 'terminal') {
        setTerminalTakeover(true)
      }
    },
    [isL1Workbench]
  )

  const commitWorkbenchTabClose = useCallback(
    (tab: RightRailTabId) => {
      const tabs = isL1Workbench ? l1Tabs : l2Tabs
      const active = isL1Workbench ? l1Active : l2Active
      const index = tabs.indexOf(tab)
      const remaining = tabs.filter(id => id !== tab)
      const next = remaining[Math.min(index, remaining.length - 1)] ?? null

      if (isL1Workbench) {
        setL1Tabs(remaining)
        if (active === tab) {
          setL1Active(next)
        }
      } else {
        setL2Tabs(remaining)
        if (active === tab) {
          setL2Active(next)
        }
      }

      if (tab.startsWith('file:')) {
        setOpenFiles(files => {
          const next = { ...files }
          delete next[tab]

          return next
        })
      }

      if (tab === 'review') {
        closeReview()
      }

      if (tab === 'terminal') {
        setTerminalTakeover(false)
      }
    },
    [isL1Workbench, l1Active, l1Tabs, l2Active, l2Tabs]
  )

  const handleWorkbenchTabClose = useCallback(
    (tab: RightRailTabId) => {
      if (!tab.startsWith('file:')) {
        commitWorkbenchTabClose(tab)

        return
      }

      const target = openFiles[tab]?.target
      const filePath = target?.path ?? ''
      const officeEditable = /\.(xlsx|docx|pptx)$/iu.test(filePath)

      if (!officeEditable) {
        if (target?.url && dirtyPreviewUrls[target.url]) {
          setDirtyCloseTab(tab)

          return
        }

        commitWorkbenchTabClose(tab)

        return
      }

      if (closingFileTabs.current.has(tab)) {
        return
      }

      closingFileTabs.current.add(tab)
      void requestGenOfficeClose(filePath)
        .then(canClose => {
          if (canClose) {
            commitWorkbenchTabClose(tab)
          }
        })
        .finally(() => closingFileTabs.current.delete(tab))
    },
    [commitWorkbenchTabClose, dirtyPreviewUrls, openFiles]
  )

  // Publish a read-only snapshot for agents: which files the human has open,
  // which editor owns them, and whether the editor has unsaved work. Debounced
  // so a burst of tab changes doesn't rewrite the file repeatedly; Office dirty
  // state is re-queried every few seconds because it lives in the webview.
  const publishOpenFilesSnapshot = useCallback(async () => {
    const activeTab = isL1Workbench ? l1Active : l2Active
    const files: Array<{
      active: boolean
      dirty: boolean | null
      editor: string
      path: string
      title: string
    }> = []

    for (const [id, entry] of Object.entries(openFiles)) {
      const path = entry.target.path ?? ''

      if (!path) {
        continue
      }

      const officeEditable = /\.(xlsx|docx|pptx)$/iu.test(path)
      const previewOnly = /\.(xls|pdf)$/iu.test(path)
      const editor = officeEditable ? 'genoffice' : previewOnly ? 'preview' : 'text'
      const dirty =
        editor === 'text'
          ? Boolean(entry.target.url && dirtyPreviewUrls[entry.target.url])
          : editor === 'genoffice'
            ? await queryGenOfficeDirty(path)
            : null

      files.push({
        active: id === activeTab,
        dirty,
        editor,
        path,
        title: entry.def.title
      })
    }

    void window.plobiDesktop?.setOpenFilesSnapshot?.({
      agentId: activeAgentId || 'l1',
      files,
      workspace: agentCwd
    })
  }, [activeAgentId, agentCwd, dirtyPreviewUrls, isL1Workbench, l1Active, l2Active, openFiles])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void publishOpenFilesSnapshot()
    }, 250)

    return () => window.clearTimeout(timer)
  }, [publishOpenFilesSnapshot])

  useEffect(() => {
    const hasOffice = Object.values(openFiles).some(({ target }) =>
      /\.(xlsx|docx|pptx)$/iu.test(target.path ?? '')
    )

    if (!hasOffice) {
      return
    }

    const timer = window.setInterval(() => {
      void publishOpenFilesSnapshot()
    }, 4000)

    return () => window.clearInterval(timer)
  }, [openFiles, publishOpenFilesSnapshot])

  // The terminal is a right-rail tab now; the store stays the single open/close
  // signal so the status stack, command palette and keybinds keep working.
  useEffect(() => {
    if (!level) {
      return
    }

    if (terminalTakeover) {
      if (isL1Workbench) {
        setL1Tabs(tabs => (tabs.includes('terminal') ? tabs : [...tabs, 'terminal']))
        setL1Active('terminal')
        setPaneOpen('agenda-timeline', true)
      } else {
        setL2Tabs(tabs => (tabs.includes('terminal') ? tabs : [...tabs, 'terminal']))
        setL2Active('terminal')
        setFileBrowserOpen(true)
      }

      return
    }

    if (l1Tabs.includes('terminal')) {
      const index = l1Tabs.indexOf('terminal')
      const remaining = l1Tabs.filter(id => id !== 'terminal')
      setL1Tabs(remaining)
      if (l1Active === 'terminal') {
        setL1Active(remaining[Math.min(index, remaining.length - 1)] ?? null)
      }
    }

    if (l2Tabs.includes('terminal')) {
      const index = l2Tabs.indexOf('terminal')
      const remaining = l2Tabs.filter(id => id !== 'terminal')
      setL2Tabs(remaining)
      if (l2Active === 'terminal') {
        setL2Active(remaining[Math.min(index, remaining.length - 1)] ?? null)
      }
    }
  }, [level, terminalTakeover])

  // Ctrl+G still drives `$reviewOpen`; the L2 workbench follows it and opens the
  // same rail column instead of a second Pane.
  useEffect(() => {
    if (!isL2 || !reviewOpen || !agentCwd.trim()) {
      return
    }

    setL2Tabs(tabs => (tabs.includes('review') ? tabs : [...tabs, 'review']))
    setL2Active('review')
    setFileBrowserOpen(true)
  }, [agentCwd, isL2, reviewOpen])

  useEffect(() => {
    if (!isL2 || reviewOpen) {
      return
    }

    setL2Active(active => (active === 'review' ? 'files' : active))
  }, [isL2, reviewOpen])

  // An Agent with no bound folder has no repo to review; keep the tab out of
  // the strip instead of letting it read the L1 remembered workspace.
  useEffect(() => {
    if (!isL2 || agentCwd.trim()) {
      return
    }

    setL2Tabs(tabs => tabs.filter(id => id !== 'review'))
    setL2Active(active => (active === 'review' ? 'files' : active))
  }, [agentCwd, isL2])

  // The L2 workbench is the counterpart of Qoder's right rail: show it once on
  // entering a workspace instead of hiding the tabs behind Ctrl+J.
  const l2WorkbenchAutoOpened = useRef(false)
  useEffect(() => {
    if (!isL2 || l2WorkbenchAutoOpened.current) {
      return
    }

    l2WorkbenchAutoOpened.current = true
    setFileBrowserOpen(true)
  }, [isL2])

  // 文件页签属于 Agent 的文件夹：切走时**收起来**（不是丢掉），切回来原样摆回去。
  // 10-31 用户报：在 A 里开着页签，切去 B 说句话再回 A，页签全没了。
  const fileTabsByWorkspace = useRef<Record<string, OpenFileTabs>>({})
  const openFilesRef = useRef(openFiles)
  openFilesRef.current = openFiles
  // 起点是空串：第一次进入某个工作区也要走「恢复」那一支，否则重启后明明存过也回不来。
  const previousWorkspaceRef = useRef<string>('')

  useEffect(() => {
    const previous = previousWorkspaceRef.current

    if (previous === agentCwd) {
      return
    }

    previousWorkspaceRef.current = agentCwd

    if (previous) {
      fileTabsByWorkspace.current[previous] = openFilesRef.current
    }

    const restored = fileTabsByWorkspace.current[agentCwd] ?? loadFileTabs(agentCwd)
    fileTabsByWorkspace.current[agentCwd] = restored

    const fileIds = Object.keys(restored).filter(id => id.startsWith('file:')) as RightRailTabId[]

    setOpenFiles(restored)
    // 两条腿的栏都按这个工作区的页签重排：先摘掉 file: 那几条，再把恢复的接回去。
    // 否则栏里会留一个没有 def 的 `file:` id：标签从栏里消失，active 还指着它。
    setL1Tabs(tabs => [...tabs.filter(id => !id.startsWith('file:')), ...fileIds])
    setL2Tabs(tabs => [...tabs.filter(id => !id.startsWith('file:')), ...fileIds])
    setL1Active(active => (active?.startsWith('file:') ? (fileIds[0] ?? null) : active))
    setL2Active(active => (active?.startsWith('file:') ? (fileIds[0] ?? 'files') : active))
  }, [agentCwd])

  // 页签一有变化就落盘，重启后还能回到原样。写失败静默（页签只是便利）。
  useEffect(() => {
    persistFileTabs(agentCwd, openFiles)
  }, [agentCwd, openFiles])

  // active 指着一条已经不在栏里的 id 时（关标签、清文件页签、附件被移走），
  // 落回第一个还在的标签——否则每个 panel 都是 hidden，整栏一片空白。
  useEffect(() => {
    if (l1Active && !l1Tabs.includes(l1Active)) {
      setL1Active(l1Tabs[0] ?? null)
    }
  }, [l1Active, l1Tabs])

  // L1 的标签栏也收文件页签（用户 10-31：日程附件点开就在这一栏看内容）。
  const l1OpenTabDefs = l1Tabs
    .map(id =>
      id === 'agenda' || id === 'browser' || id === 'terminal' ? workbenchTabDefs[id] : openFiles[id]?.def
    )
    .filter((def): def is RightRailTabDef => Boolean(def))
  const l2OpenTabDefs = l2Tabs
    .map(id =>
      id === 'files' ||
      id === 'review' ||
      id === 'browser' ||
      id === 'agenda' ||
      id === 'terminal' ||
      id === 'progress'
        ? workbenchTabDefs[id]
        : openFiles[id]?.def
    )
    .filter((def): def is RightRailTabDef => Boolean(def))
  const fileTabContents: Record<string, ReactNode> = Object.fromEntries(
    Object.entries(openFiles).map(([id, { target }]) => {
      const media = mediaKind(target.path ?? '')
      const isMedia = media === 'audio' || media === 'video'
      const isArchive = /\.zip$/iu.test(target.path ?? '')
      const is3d = isModelPath(target.path ?? '')
      const office = !isMedia && !isArchive && !is3d && fileKindForPath(target.path ?? '') !== 'other'

      return [
        id,
        isMedia ? (
          <Suspense
            fallback={
              <div className="flex min-h-0 flex-1 items-center justify-center">
                <PageLoader label={t.common.loading} />
              </div>
            }
            key={id}
          >
            <MediaPreview labels={t.workbench.office} path={target.path ?? ''} />
          </Suspense>
        ) : isArchive ? (
          <Suspense
            fallback={
              <div className="flex min-h-0 flex-1 items-center justify-center">
                <PageLoader label={t.common.loading} />
              </div>
            }
            key={id}
          >
            <ArchivePreview labels={t.workbench.office} path={target.path ?? ''} />
          </Suspense>
        ) : is3d ? (
          <Suspense
            fallback={
              <div className="flex min-h-0 flex-1 items-center justify-center">
                <PageLoader label={t.common.loading} />
              </div>
            }
            key={id}
          >
            <ThreePreview
              labels={{
                loading: t.common.loading,
                openExternal: t.workbench.office.openExternal,
                readOnly: t.workbench.office.readOnly,
                unavailable: t.preview.unavailable
              }}
              path={target.path ?? ''}
            />
          </Suspense>
        ) : office ? (
          <Suspense
            fallback={
              <div className="flex min-h-0 flex-1 items-center justify-center">
                <PageLoader label={t.common.loading} />
              </div>
            }
            key={id}
          >
            <OfficeFilePreview labels={t.workbench.office} onOpenFile={openFileTab} target={target} />
          </Suspense>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto" key={id}>
            <LocalFilePreview reloadKey={0} target={target} />
          </div>
        )
      ]
    })
  )

  useEffect(() => {
    if (!level) {
      return
    }

    document.title = secretaryShellLabel(level, t.console.chiefSecretary, consoleAgents)
  }, [consoleAgents, level, t.console.chiefSecretary])

  useEffect(() => {
    if (level !== 'l1') {
      return
    }

    const profile = normalizeProfileKey($activeGatewayProfile.get() || 'default')

    if (readL1MainSessionId(profile)) {
      return
    }

    const id = selectedStoredSessionId || activeSessionId

    if (id) {
      writeL1MainSessionId(profile, id)
    }
  }, [activeSessionId, level, selectedStoredSessionId])

  const titlebarToolGroups = useGroupRegistry<TitlebarTool>()
  const statusbarItemGroups = useGroupRegistry<StatusbarItem>()
  const setTitlebarToolGroup = titlebarToolGroups.set
  const setStatusbarItemGroup = statusbarItemGroups.set

  const {
    activeSessionIdRef,
    ensureSessionState,
    runtimeIdByStoredSessionIdRef,
    selectedStoredSessionIdRef,
    sessionStateByRuntimeIdRef,
    syncSessionStateToView,
    updateSessionState
  } = useSessionStateCache({
    activeSessionId,
    busyRef,
    selectedStoredSessionId,
    setAwaitingResponse,
    setBusy,
    setMessages
  })

  const { connectionRef, gatewayRef, requestGateway } = useGatewayRequest()

  useEffect(() => {
    window.plobiDesktop?.setPreviewShortcutActive?.(
      Boolean(secretaryShellOpen && previewRailContent)
    )
  }, [previewRailContent, secretaryShellOpen])

  // Leaving the secretary shell (Artifacts / Skills / redirect bounce): force
  // the preview column shut so persisted file tabs cannot paint full-bleed
  // over an empty PaneMain. Tabs stay in storage for the next L2 visit.
  useEffect(() => {
    if (!secretaryShellOpen) {
      setPaneOpen(PREVIEW_PANE_ID, false)
    }
  }, [secretaryShellOpen])

  useEffect(() => {
    startUpdatePoller()
    const unsubscribe = window.plobiDesktop?.onOpenUpdatesRequested?.(() => openUpdatesWindow())

    return () => {
      unsubscribe?.()
      stopUpdatePoller()
    }
  }, [])

  // Remember the open chat so a relaunch reopens it instead of an empty new-chat.
  useEffect(() => {
    if (routedSessionId) {
      setRememberedSessionId(routedSessionId)
    }
  }, [routedSessionId])

  // Restore that chat once, on cold start only (we're at the new-chat route and
  // haven't navigated yet). A dead/deleted id self-clears via the exhausted latch
  // below, so we never boot-loop into an error screen.
  //
  // U6: cold start now lands on `/` = the S1 console (spec §3.5), so this only
  // fires when the app boots straight into `/new` (the fresh-draft chat). The
  // remembered session stays one click away in the sidebar instead of hijacking
  // the landing surface.
  const restoredLastSessionRef = useRef(false)
  useEffect(() => {
    if (restoredLastSessionRef.current) {
      return
    }

    restoredLastSessionRef.current = true
    const last = getRememberedSessionId()

    if (last && location.pathname === NEW_CHAT_ROUTE) {
      navigate(sessionRoute(last), { replace: true })
    }
  }, [location.pathname, navigate])

  useEffect(() => {
    if (resumeExhaustedSessionId && getRememberedSessionId() === resumeExhaustedSessionId) {
      setRememberedSessionId(null)
    }
  }, [resumeExhaustedSessionId])

  // Notification click: the main process already focused the window; jump to its
  // session. Notifications are tagged with the gateway *runtime* session id, but
  // the chat route is keyed by the *stored* id — navigating with the runtime id
  // resumes a non-existent stored session ("session not found") and strands the
  // user. Translate runtime -> stored before navigating.
  useEffect(() => {
    const unsubscribe = window.plobiDesktop?.onFocusSession?.(sessionId => {
      if (sessionId) {
        navigate(sessionRoute(storedSessionIdForNotification(sessionId, runtimeIdByStoredSessionIdRef.current)))
      }
    })

    return () => unsubscribe?.()
  }, [navigate, runtimeIdByStoredSessionIdRef])

  // Notification action button (Approve/Reject) — resolve in place, no navigation.
  useEffect(() => {
    const unsubscribe = window.plobiDesktop?.onNotificationAction?.(({ actionId, sessionId }) => {
      void respondToApprovalAction(sessionId ?? null, actionId)
    })

    return () => unsubscribe?.()
  }, [])

  // plobi:// deep links (e.g. a docs "Send to App" button for an automation blueprint).
  // Build the equivalent /blueprint slash command from the payload and drop
  // it into the composer — the user reviews/edits, then sends; the agent (or
  // the shared command handler) creates the job. Signal readiness so a link
  // that arrived during boot is flushed exactly once.
  useEffect(() => {
    const unsubscribe = window.plobiDesktop?.onDeepLink?.(payload => {
      if (!payload || payload.kind !== 'blueprint' || !payload.name) {
        return
      }

      const slots = Object.entries(payload.params || {})
        .map(([k, v]) => {
          const sval = /\s/.test(v) ? `"${v.replace(/"/g, '\\"')}"` : v

          return `${k}=${sval}`
        })
        .join(' ')

      const command = `/blueprint ${payload.name}${slots ? ' ' + slots : ''}`
      requestComposerInsert(command, { mode: 'block', target: 'main' })
      requestComposerFocus('main')
    })

    // Tell the main process the renderer is ready to receive deep links.
    void window.plobiDesktop?.signalDeepLinkReady?.()

    return () => unsubscribe?.()
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.shiftKey || event.key.toLowerCase() !== 'w' || (!event.metaKey && !event.ctrlKey)) {
        return
      }

      // Terminal focused: ⌘W closes the active terminal. Ctrl+W is left untouched
      // for the shell's werase, and nothing else may steal ⌘/Ctrl+W from a
      // focused terminal (so it never closes a preview tab out from under it).
      if (isFocusWithin('[data-terminal]')) {
        if (event.metaKey && !event.ctrlKey) {
          event.preventDefault()
          event.stopPropagation()
          closeActiveTerminal()
        }

        return
      }

      // Otherwise ⌘/Ctrl+W closes the active preview tab when one is open.
      if ($filePreviewTarget.get() || $previewTarget.get()) {
        event.preventDefault()
        event.stopPropagation()
        closeActiveRightRailTab()
      }
    }

    const unsubscribe = window.plobiDesktop?.onClosePreviewRequested?.(closeActiveRightRailTab)

    window.addEventListener('keydown', onKeyDown, { capture: true })

    return () => {
      unsubscribe?.()
      window.removeEventListener('keydown', onKeyDown, { capture: true })
    }
  }, [])

  const {
    loadMoreMessagingForPlatform,
    loadMoreSessions,
    loadMoreSessionsForProfile,
    refreshCronJobs,
    refreshMessagingSessions,
    refreshSessions
  } = useSessionListActions({ profileScope })

  // Another window mutated the shared session list (e.g. a chat started in the
  // pop-out). Re-pull so the sidebar reflects it. Pop-outs have no sidebar, so
  // only real windows bother.
  useEffect(() => {
    if (isSecondaryWindow()) {
      return
    }

    return onSessionsChanged(() => void refreshSessions().catch(() => undefined))
  }, [refreshSessions])

  const { toggleSelectedPin } = useSessionPinToggle()

  const { inferenceStatus, statusSnapshot } = useStatusSnapshot(gatewayState, requestGateway)

  const updateActiveSessionRuntimeInfo = useCallback(
    (info: { branch?: string; cwd?: string }) => {
      const sessionId = activeSessionIdRef.current

      if (!sessionId) {
        return
      }

      updateSessionState(sessionId, state => ({
        ...state,
        branch: info.branch ?? state.branch,
        cwd: info.cwd ?? state.cwd
      }))
    },
    [activeSessionIdRef, updateSessionState]
  )

  const { refreshProjectBranch } = useCwdActions({
    activeSessionId,
    activeSessionIdRef,
    onSessionRuntimeInfo: updateActiveSessionRuntimeInfo,
    requestGateway
  })

  const { refreshPlobiConfig, sttEnabled, voiceMaxRecordingSeconds } = usePlobiConfig({
    activeSessionIdRef,
    refreshProjectBranch
  })

  const { refreshCurrentModel, selectModel, updateModelOptionsCache } = useModelControls({
    activeSessionId,
    isAgentSession: isL2,
    scope: chatScope,
    queryClient,
    requestGateway
  })

  const openProviderSettings = useCallback(() => {
    navigate(`${SETTINGS_ROUTE}?tab=providers`)
  }, [navigate])

  const modelMenuContent = useMemo(
    () =>
      gatewayState === 'open' ? (
        <ModelMenuPanel
          gateway={gatewayRef.current || undefined}
          onSelectModel={selectModel}
          requestGateway={requestGateway}
        />
      ) : null,
    [gatewayRef, gatewayState, requestGateway, selectModel]
  )

  useContextSuggestions({
    activeSessionId,
    activeSessionIdRef,
    currentCwd,
    gatewayState,
    requestGateway
  })

  const { hydrateFromStoredSession, refreshActiveMessagingTranscript } = useTranscriptRehydration({
    activeSessionIdRef,
    busyRef,
    selectedStoredSessionIdRef,
    updateSessionState
  })

  const {
    appendAssistantDelta,
    appendReasoningDelta,
    completeAssistantMessage,
    failAssistantMessage,
    handleGatewayEvent
  } = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession,
    queryClient,
    refreshPlobiConfig,
    refreshSessions,
    sessionStateByRuntimeIdRef,
    updateSessionState
  })

  const { handleDesktopGatewayEvent, restartPreviewServer } = usePreviewRouting({
    activeSessionIdRef,
    baseHandleGatewayEvent: handleGatewayEvent,
    currentCwd,
    currentView,
    requestGateway,
    routedSessionId,
    selectedStoredSessionId
  })

  const {
    archiveSession,
    branchCurrentSession,
    branchStoredSession,
    createBackendSessionForSend,
    openSettings,
    removeSession,
    resumeSession,
    selectSidebarItem,
    startFreshSessionDraft
  } = useSessionActions({
    activeSessionId,
    activeSessionIdRef,
    busyRef,
    creatingSessionRef,
    ensureSessionState,
    getRouteToken,
    navigate,
    requestGateway,
    runtimeIdByStoredSessionIdRef,
    selectedStoredSessionId,
    selectedStoredSessionIdRef,
    sessionStateByRuntimeIdRef,
    syncSessionStateToView,
    updateSessionState
  })

  const { startFreshConversation, prepareFreshDraftInPlace } = useConversationReset({
    busyRef,
    activeSessionIdRef,
    selectedStoredSessionIdRef,
    level,
    startFreshSessionDraft
  })

  // Single global listener for every rebindable hotkey (incl. profile switching)
  // plus the on-screen keybind editor's capture mode.
  useKeybinds({
    // 裁定 38/39 (WP-AGENT-MOUTH): ⌘N must not open a second conversation for an
    // Agent. On a secretary route it restarts the one mouth that Agent already
    // has, in place; off it (mainline session chat) it still starts a new chat.
    startFreshSession: startFreshConversation,
    toggleCommandCenter,
    toggleSelectedPin
  })

  // Swapping the live gateway to another profile must re-pull that profile's
  // global model + active-profile pill. Both are nanostores, so the blanket
  // invalidateQueries() the profile store fires on swap doesn't touch them —
  // without this the statusbar keeps showing the previous profile's model
  // (the "forgets the LLM setting" report). gatewayState stays 'open' across a
  // swap (background sockets persist), so the open→open effect won't re-run.
  useProfileScopeSync({
    activeSessionId,
    chatScope,
    freshDraftReady,
    gatewayState,
    refreshCurrentModel,
    refreshPlobiConfig,
    refreshSessions
  })

  const composer = useComposerActions({
    activeSessionId,
    currentCwd,
    requestGateway
  })

  const branchInNewChat = useCallback(
    async (messageId?: string) => {
      const branched = await branchCurrentSession(messageId)

      if (branched) {
        await refreshSessions().catch(() => undefined)
      }

      return branched
    },
    [branchCurrentSession, refreshSessions]
  )

  const { dismissError } = useMessageErrorDismiss({ activeSessionIdRef, updateSessionState })

  const { startSessionInWorkspace } = useWorkspaceSessionStart({ requestGateway, startFreshSessionDraft })

  const handleSkinCommand = useSkinCommand()

  const {
    cancelRun,
    editMessage,
    handleThreadMessagesChange,
    reloadFromMessage,
    restoreToMessage,
    steerPrompt,
    submitText,
    transcribeVoiceAudio
  } = usePromptActions({
    activeSessionId,
    activeSessionIdRef,
    branchCurrentSession: branchInNewChat,
    busyRef,
    createBackendSessionForSend,
    getRouteToken,
    handleSkinCommand,
    openMemoryGraph: openStarmap,
    refreshSessions,
    requestGateway,
    resumeStoredSession: resumeSession,
    selectedStoredSessionIdRef,
    // `/new` and its `/reset` alias restart the mouth this shell is showing
    // instead of routing to the retired `/new` chat surface.
    startFreshSessionDraft: startFreshConversation,
    sttEnabled,
    updateSessionState
  })

  // The popped-out pet drives two actions back into the app: send a prompt, and
  // open the most recent thread. Both are registered ONCE through refs that track
  // the latest callbacks — re-registering on every `submitText`/`resumeSession`
  // identity change left a brief window where the handler was nulled (cleanup
  // before re-register), which could drop a submit fired from the overlay (e.g.
  // creating a session from the new-session screen). The ref form keeps a stable,
  // always-current handler. Primary window only — it owns the overlay.
  const submitTextRef = useRef(submitText)
  submitTextRef.current = submitText
  const cancelRunRef = useRef(cancelRun)
  cancelRunRef.current = cancelRun
  const resumeSessionRef = useRef(resumeSession)
  resumeSessionRef.current = resumeSession
  const requestGatewayRef = useRef(requestGateway)

  // Stable callbacks backed by refs — safe to pass into useMemo/useEffect
  // without causing re-computation on every render.
  const stableCancel = useCallback(() => void cancelRunRef.current(), [])

  const stableSubmit = useCallback(
    (text: string, options?: { attachments?: ComposerAttachment[]; fromQueue?: boolean }) =>
      submitTextRef.current(text, options),
    []
  )

  requestGatewayRef.current = requestGateway
  const refreshSessionsRef = useRef(refreshSessions)
  refreshSessionsRef.current = refreshSessions

  // WP-C: bind the live gateway + transcript to the secretary identity of the
  // current route so L1 and L2 never share one conversation (ARCH-RULINGS 裁定
  // 1/3). L1 → master when that profile exists; L2 → the agent's profile + its
  // latest session (or a fresh in-place draft). Re-runs on every L1↔L2 switch.
  // Uses resumeSessionRef so identity churn on the callback does not re-bind
  // mid-conversation.
  useEffect(() => {
    if (!level || gatewayState !== 'open') {
      return
    }

    let cancelled = false

    void (async () => {
      if (level === 'l1') {
        // 回 L1 就离开 Agent 作用域：右栏回到 L1 自己的工作目录（remembered /
        // 配置的默认目录），而不是停在刚才那个 Agent 的文件夹里。
        releaseAgentCwd()
        await refreshConsoleAgents()

        if (cancelled) {
          return
        }

        try {
          await refreshProfiles()
        } catch {
          return
        }

        if (cancelled) {
          return
        }

        // 裁定 6: no `master` profile → stay on the current gateway. Never
        // `ensureGatewayProfile('master')` for a missing profile (WP-G3).
        // Either way we MUST still bind a session for the active profile —
        // returning early left a stale L2 runtime id in the center while the
        // sidebar showed "No sessions yet", so the next send 404'd.
        let masterBound: 'bound-master' | 'stayed'

        try {
          masterBound = await bindL1GatewayIfMasterExists({
            ensureGatewayProfile,
            profiles: $profiles.get()
          })
        } catch {
          return
        }

        if (cancelled) {
          return
        }

        let profileForSessions: string

        if (masterBound === 'bound-master') {
          profileForSessions = 'master'
        } else {
          // After L2 the live gateway is often still on the agent profile.
          // Resolve back to the L1 home (remembered default/custom) before
          // picking the mainline — otherwise the center keeps the L2 chat.
          const agentProfiles = $consoleAgents
            .get()
            .map(agent => normalizeProfileKey((agent.profile || '').trim()))
            .filter(Boolean)

          profileForSessions = resolveL1HomeProfile({
            activeProfile: $activeGatewayProfile.get(),
            agentProfiles,
            rememberedHome: l1GatewayProfileRef.current
          })
          const live = normalizeProfileKey($activeGatewayProfile.get() || 'default')

          if (live !== profileForSessions) {
            try {
              await ensureGatewayProfile(profileForSessions)
            } catch {
              prepareFreshDraftInPlace()

              return
            }

            if (cancelled) {
              return
            }

            if ($gatewayState.get() !== 'open') {
              prepareFreshDraftInPlace()

              return
            }
          }
        }

        await refreshSessionsRef.current().catch(() => undefined)

        if (cancelled) {
          return
        }

        // 总秘书**只有一张嘴**（用户 10-30：「侧栏没有会话概念了。就一张嘴。永远都只有一张嘴。」）。
        // 那张嘴＝这个家上**最新的一条有内容的会话**——不再读"记住的主线"（它会
        // 把嘴钉在几天前那条上，之后每条新对话都被归档掉 ⇒「聊天记录不见了」）。
        const existing = pickL1Mouth($sessions.get(), profileForSessions)

        if (existing) {
          await resumeSessionRef.current(existing.id)
        } else {
          prepareFreshDraftInPlace()
        }

        if (cancelled) {
          return
        }

        l1GatewayProfileRef.current = profileForSessions

        return
      }

      const agentId = level.l2

      // 点下去这一帧就要有态：名字从名册缓存里拿（一次网络都不等）。
      const rosterRow = $consoleAgents.get().find(a => a.id === agentId)

      $agentEnter.set(beginEntering(rosterRow?.name || rosterRow?.id || ''))

      // 名册里这一行没有 profile ⇒ 它是**数据记录**（`l2_course`，裁定 40：课是数据不是分身），
      // 不是"一个还没连上的分身"。以前这里回退成 agentId，等于替它造一个 profile 名，
      // 主进程真会去起 `plobi -p <那个名字>`：ASCII 名退 1（Profile does not exist）、
      // 非 ASCII 名被 CLI 的 -p 预扫丢掉退 2，五门课各自一场重启循环
      // （2026-10-13 现核 desktop.log 单「电工电子学」2767 行 failed）。
      // 注册表那一半已改成数据角色一律回空串（plobi/agents/registry.py::profile_name）；
      // 这一半是**客户端不许自己抄一条回退**（裁定 94 同族）。
      let profile =
        $consoleAgents.get().find(agent => agent.id === agentId)?.profile?.trim() ?? ''

      let sessionId = ''
      let overviewFailed = false
      // R-013 (裁定 20): the bound folder served by the overview. Stays null
      // when the overview itself failed — then the cwd is left untouched
      // rather than pretending we know the binding.
      let agentProjectPath: null | string = null

      try {
        const overview = await getAgentOverview(agentId)

        if (cancelled) {
          return
        }

        sessionId = overview.sessionId?.trim() || ''
        profile = overview.agent.profile?.trim() || profile
        agentProjectPath = (overview.projectPath ?? '').trim()
      } catch {
        // Offline / mock miss — still try the registry-default profile name.
        overviewFailed = true
      }

      // Mount the agent's bound folder for the right-rail file tree.
      //
      // 2026-10-21 用户报「不管进什么 Agent，右边的目录都是 Aura 的」——两条
      // 一起修：① 这里改成 `setAgentCwd`（作用域绑定，不写 L1 的 remembered，
      // 且让 `refreshPlobiConfig` 不许再用 `terminal.cwd` 盖它）；② 概览失败
      // 时**显式清空**，不再"保留不动"——上一版注释写的是"left untouched"，
      // 结果就是继承上一个 Agent（通常是 Aura）的目录，看起来像"目录不跟着走"。
      // 空目录（butler 类、没绑定文件夹）也走同一条：清空，显示空态提示。
      const mountAgentCwd = () => {
        setAgentCwd(overviewFailed ? '' : (agentProjectPath ?? ''))
      }

      if (cancelled) {
        return
      }

      // The agent's profile is registry-authoritative: it names a pool backend
      // the Electron main process lazily spawns/reuses (typically on a
      // non-default port, not the primary's). Do NOT gate the bind on
      // `$profiles` (the user-facing list from GET /api/profiles) — agent-pool
      // profiles are never in that list, so the old gate silently skipped the
      // bind and the first L2 message's `session.create` landed on the PRIMARY
      // backend instead of the agent's, leaving `profiles/<agent>/state.db`
      // empty and the agent log with no rounds (the `/agent/simulation` "send
      // swallowed" bug: transcript shows nothing because the turn never reached
      // the agent's dispatch). Bind unconditionally; the registry is the source
      // of truth for which profiles exist.
      const previousProfile = $activeGatewayProfile.get()

      // 数据条目：不绑后端、不替它编一个家。走与"绑不上"同一条既有路径——
      // 留在原地、开一张新草稿，不排任何重试。区别只在过渡那句说什么：
      // 概览取不到要说"后端没答上"，取到了而这本来就没后端要说中性那句。
      if (!profile) {
        if (overviewFailed) {
          $agentEnter.set(enterFailure('no-overview'))
        } else {
          $agentEnter.set(endEntering())
        }

        // 数据条目也要把右栏钉到**它自己**的目录（或清空），不留在上一个
        // Agent 的文件夹里——否则「点了这个、右栏还是上一个」看着就像坏了。
        setAgentCwd(agentProjectPath ?? '')
        prepareFreshDraftInPlace()

        return
      }

      // Refresh L1 home from the profile we're leaving when it is still an L1
      // profile; keep the remembered home when hopping L2→L2 (agent→agent).
      l1GatewayProfileRef.current = resolveL1HomeProfile({
        activeProfile: previousProfile,
        agentProfiles: $consoleAgents
          .get()
          .map(agent => normalizeProfileKey((agent.profile || '').trim()))
          .filter(Boolean),
        rememberedHome: l1GatewayProfileRef.current
      })

      try {
        await ensureGatewayProfile(profile)
      } catch {
        $agentEnter.set(enterFailure('spawn'))
        await ensureGatewayProfile(previousProfile).catch(() => undefined)
        // 没绑上就回到 L1 那一屏，cwd 也跟着回 L1——别让它停在半路的 Agent 目录。
        releaseAgentCwd()
        prepareFreshDraftInPlace()

        return
      }

      // `ensureGatewayProfile` activates the secondary socket even when the pool
      // backend is unreachable (it schedules a reconnect and leaves the gateway
      // connecting/closed). Verify the bind actually opened before trusting it —
      // otherwise the shell strands on a dead socket and L2 looks "unenterable".
      // Revert to the prior profile and show an empty draft so `/agent/:id`
      // stays reachable.
      if ($gatewayState.get() !== 'open') {
        $agentEnter.set(enterFailure('bind'))
        await ensureGatewayProfile(previousProfile).catch(() => undefined)
        releaseAgentCwd()
        prepareFreshDraftInPlace()

        return
      }

      if (cancelled) {
        return
      }

      // Profile swap updates `$profileScope` immediately; refresh using the
      // live atom so N3's injected L2 session is in `$sessions` before resume.
      await refreshSessionsRef.current().catch(() => undefined)

      if (cancelled) {
        return
      }

      // Only resume a session the desktop can see after that refresh. Overview
      // mock ids (and stale REST ids) would otherwise burn a failed resume
      // against the freshly switched profile.
      const knownSession = findKnownOverviewSession($sessions.get(), sessionId)

      if (knownSession && sessionId) {
        try {
          await resumeSessionRef.current(sessionId)

          mountAgentCwd()

          return
        } catch {
          // Fall through to an empty draft on this profile.
        }
      }

      if (cancelled) {
        return
      }

      prepareFreshDraftInPlace()
      mountAgentCwd()
    })().catch(() => undefined).finally(() => {
      // 六条出口走不到终态的那几条（含概览后提前 return 的分支）由这里兜住：
      // **只有还挂在进行中才清**，失败那句留在场上给人读完。
      if (!cancelled) {
        settleAgentEnter()
      }
    })

    return () => {
      cancelled = true
    }
  }, [level, gatewayState, prepareFreshDraftInPlace])

  usePetBridge({
    requestGatewayRef,
    resumeSessionRef,
    submitTextRef
  })

  useGatewayBoot({
    handleGatewayEvent: handleDesktopGatewayEvent,
    onConnectionReady: c => {
      connectionRef.current = c
    },
    onGatewayReady: g => {
      gatewayRef.current = g
    },
    refreshPlobiConfig,
    refreshSessions
  })

  useBackgroundPolling({
    gatewayState,
    refreshActiveMessagingTranscript,
    refreshCronJobs,
    refreshMessagingSessions
  })

  useRouteResume({
    activeSessionId,
    activeSessionIdRef,
    creatingSessionRef,
    currentView,
    freshDraftReady,
    gatewayState,
    locationPathname: location.pathname,
    resumeSession,
    resumeFailedSessionId,
    resumeExhaustedSessionId,
    routedSessionId,
    runtimeIdByStoredSessionIdRef,
    selectedStoredSessionId,
    selectedStoredSessionIdRef,
    startFreshSessionDraft
  })

  const { leftStatusbarItems, statusbarItems } = useStatusbarItems({
    agentsOpen,
    commandCenterOpen,
    extraLeftItems: statusbarItemGroups.flat.left,
    extraRightItems: statusbarItemGroups.flat.right,
    gatewayState,
    inferenceStatus,
    openAgents,
    freshDraftReady,
    openCommandCenterSection,
    requestGateway,
    statusSnapshot,
    toggleCommandCenter
  })

  // 左下那排状态图标搬进左栏（10-31）：展开时横排，收起时只剩「⋯」。
  const systemTools = useSystemTools(openSettings)
  const dockTools = systemTools.map(tool => ({
    disabled: tool.disabled,
    hidden: tool.hidden,
    href: tool.href,
    icon: tool.icon,
    id: `system:${tool.id}`,
    label: tool.title ?? tool.label,
    onSelect: tool.onSelect,
    title: tool.title ?? tool.label,
    to: tool.to,
    variant: 'action' as const
  }))
  const sidebarFooterItems = [...dockTools, ...leftStatusbarItems, ...statusbarItems]

  const sidebar = (
    <ChatSidebar
      footerItems={sidebarFooterItems}
      currentView={currentView}
      level={level}
      onArchiveSession={sessionId => void archiveSession(sessionId)}
      onDeleteSession={sessionId => void removeSession(sessionId)}
      onNavigate={selectSidebarItem}
      onResumeSession={sessionId => {
        // Secretary shells keep the URL (`/` / `/agent/:id`) and resume in place.
        // Navigating to `/:sessionId` nulls shellLevel and bounces home (WP-E).
        if (level) {
          void resumeSessionRef.current(sessionId)

          return
        }

        navigate(sessionRoute(sessionId))
      }}
    />
  )

  // The persistent xterm layer (one host per terminal tab), CSS-overlaid onto the
  // pane's <TerminalSlot />. Lives in main's stacking context (not the root overlay
  // layer) so pane resize handles still paint above it. Terminals own their state
  // (incl. a snapshotted cwd) independent of the session, so switching sessions
  // never rebuilds or closes them; toggling the pane never rebuilds the shells.
  const mainOverlays = <PersistentTerminal onAddSelectionToChat={composer.addTerminalSelectionAttachment} />

  const overlays = (
    <>
      <RemoteDisplayBanner />
      {!isSecondaryWindow() && <DesktopInstallOverlay />}
      {!isSecondaryWindow() && (
        <DesktopOnboardingOverlay
          enabled={gatewayState === 'open'}
          onCompleted={() => {
            void refreshPlobiConfig()
            void refreshCurrentModel()
            void queryClient.invalidateQueries({ queryKey: ['model-options'] })
          }}
          requestGateway={requestGateway}
        />
      )}
      <ModelPickerOverlay gateway={gatewayRef.current || undefined} onSelect={selectModel} />
      <SessionPickerOverlay onResume={resumeSession} />
      <ModelVisibilityOverlay gateway={gatewayRef.current || undefined} onOpenProviders={openProviderSettings} />
      <UpdatesOverlay />
      <MindPendingOverlay />
      <GatewayConnectingOverlay />
      <BootFailureOverlay />
      <CommandPalette />
      <DiaryCaptureOverlay />
      <PetGenerateOverlay />
      <SessionSwitcher />
      <FileActionDialogs />
      <RemoteFolderPicker />
      <ConfirmDialog
        confirmLabel={t.preview.discardCloseConfirm}
        description={t.preview.discardCloseBody}
        destructive
        dismissOnConfirm
        onClose={() => setDirtyCloseTab(null)}
        onConfirm={() => {
          if (dirtyCloseTab) {
            commitWorkbenchTabClose(dirtyCloseTab)
          }

          setDirtyCloseTab(null)
        }}
        open={dirtyCloseTab !== null}
        title={t.preview.unsavedChanges}
      />

      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsView
            gateway={gatewayRef.current}
            onClose={closeOverlayToPreviousRoute}
            onConfigSaved={() => {
              void refreshPlobiConfig()
              void refreshCurrentModel()
              void queryClient.invalidateQueries({ queryKey: ['model-options'] })
            }}
            onMainModelChanged={(provider, model) => {
              setCurrentProvider(provider)
              setCurrentModel(model)
              updateModelOptionsCache(provider, model, true)
              void refreshCurrentModel()
              void queryClient.invalidateQueries({ queryKey: ['model-options'] })
            }}
          />
        </Suspense>
      )}

      {commandCenterOpen && (
        <Suspense fallback={null}>
          <CommandCenterView
            initialSection={commandCenterInitialSection}
            onClose={closeOverlayToPreviousRoute}
            onDeleteSession={removeSession}
            onNavigateRoute={path => navigate(path)}
            onOpenSession={sessionId => navigate(sessionRoute(sessionId))}
          />
        </Suspense>
      )}

      {agentsOpen && (
        <Suspense fallback={null}>
          <AgentsView onClose={closeOverlayToPreviousRoute} />
        </Suspense>
      )}

      {cronOpen && (
        <Suspense fallback={null}>
          <CronView
            onClose={closeOverlayToPreviousRoute}
            onOpenSession={sessionId => navigate(sessionRoute(sessionId))}
          />
        </Suspense>
      )}

      {agendaOpen && (
        <Suspense fallback={null}>
          <AgendaView onClose={closeOverlayToPreviousRoute} />
        </Suspense>
      )}

      {starmapOpen && (
        <Suspense fallback={null}>
          <StarmapView onClose={closeOverlayToPreviousRoute} />
        </Suspense>
      )}
    </>
  )

  // WP-A: the L1/L2 center reuses the base Thread + ChatBar through the
  // console's `ChatSurface`. WP-C wires its submit to the REAL gateway
  // (`submitText` / `cancelRun`) so the L1 center streams through the base
  // session store — no mock transport, no `POST /api/chat` (裁定 2).
  // A null level (unknown path) bounces home like the old catch-all. The wrapper
  // provides the same titlebar clearance ConsoleHome's root used.
  // Stable component type (not a useMemo'd element) so L1↔L2 and model-picker
  // updates do not remount the center pane.

  // WP-E: the legacy pure-chat routes (`/new`, `/:sessionId`) are gone — every
  // conversation lives in the L1 or L2 secretary shell now. Any navigate() to
  // the old routes falls through to the `*` secretary route below (null level →
  // bounced home), and `/console` still redirects to `/` (WP-A).
  const sidebarSide = panesFlipped ? 'right' : 'left'
  const railSide = panesFlipped ? 'left' : 'right'

  const previewPane = (
    <Pane
      disabled={!secretaryShellOpen || !previewRailContent}
      id={PREVIEW_PANE_ID}
      key="preview"
      minWidth={PREVIEW_RAIL_MIN_WIDTH}
      resizable
      side={railSide}
      width={PREVIEW_RAIL_PANE_WIDTH}
    >
      {secretaryShellOpen && previewRailContent ? (
        <ChatPreviewRail
          agentId={activeAgentId}
          onAddSelectionToChat={composer.addTerminalSelectionAttachment}
          onRestartServer={restartPreviewServer}
          setTitlebarToolGroup={setTitlebarToolGroup}
        />
      ) : null}
    </Pane>
  )

  // 互换后这块栏内容进主区：不再有侧边描边、也不用再让顶栏（卡本身已经让过了）。
  const workbenchAsideClass = cn(
    'relative flex h-full w-full min-w-0 flex-col overflow-hidden text-(--ui-text-tertiary)',
    swapped
      ? 'bg-(--ui-sidebar-surface-background)'
      : cn(
          'border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) pt-(--titlebar-height)',
          panesFlipped
            ? 'border-r shadow-[inset_-0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
            : 'border-l shadow-[inset_0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
        )
  )

  const workbenchFilesContent = agentCwd.trim() ? (
    <RightSidebarPane
      cwd={agentCwd}
      embedded
      key={agentCwd}
      onActivateFile={path => composer.insertContextPathInlineRef(path)}
      onActivateFolder={path => composer.insertContextPathInlineRef(path, true)}
      onOpenFile={openFileTab}
      onToggleWorkspaceSidebar={() => setFileBrowserOpen(false)}
    />
  ) : null
  const workbenchReviewContent = <ReviewPane embedded key={agentCwd || 'no-cwd'} />
  // 点开的日程（用户 10-31）：右栏就地换成这条的详情，返回才回到时间轴。
  // 动作（勾清单、改字段）会把新行写回共享 store，所以渲染时优先取 store 那份；
  // 别的日子那根轴上的行不在 store 里，就退回点进来时那一份。
  const shownRailEvent = railEvent
    ? (agendaEvents.find(event => event.id === railEvent.id) ?? railEvent)
    : null
  const agendaTabContent = shownRailEvent ? (
    <RailEventDetail
      event={shownRailEvent}
      onBack={() => setRailEvent(null)}
      onOpenAttachment={openAgendaAttachment}
    />
  ) : (
    <>
      {agendaAdding ? <AddEventForm onClose={() => setAgendaAdding(false)} /> : null}
      {/* 「加一条」原来挂在标签栏的 trailing 上，跟「新开标签」那个 + 长得一模一样
          （用户 10-31：两个加号分不清）。挪进日程内容里、带文字，标签栏只剩一个 +。 */}
      <div className="flex shrink-0 items-center justify-end px-2 pt-1">
        <Button
          aria-label={t.console.timeline.addEvent}
          className="gap-1"
          onClick={() => setAgendaAdding(open => !open)}
          size="xs"
          title={t.console.timeline.addEvent}
          variant="ghost"
        >
          <Codicon name="add" size="0.75rem" />
          {t.console.timeline.addEvent}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {/* 裁定 40: the today axis (with its "now" line) MUST mount even
           when the rail is still loading or the day payload is empty.
           Otherwise a fresh relaunch lands the user on the loader /
           error / empty-state and they never see today's axis. */}
        {agendaLoading && agendaEvents.length === 0 ? (
          <div aria-hidden="true" className="flex justify-center py-4">
            <PageLoader label={t.console.timeline.title} />
          </div>
        ) : null}
        {agendaError && agendaEvents.length === 0 ? (
          <div className="flex justify-center pb-2">
            <Button onClick={() => void refreshAgendaTimeline()} size="xs" variant="ghost">
              {t.console.timeline.retry}
            </Button>
          </div>
        ) : null}
        <Timeline
          avoidWindows={agendaAvoidWindows}
          events={agendaEvents}
          onSelectEvent={setRailEvent}
        />
        {agendaRhythmOff && !agendaLoading ? (
          <p className="px-2 pt-1 text-[0.7rem] text-muted-foreground/70" data-testid="timeline-rhythm-off">
            {t.agenda.rhythmOff}
          </p>
        ) : null}
      </div>
    </>
  )

  // 右栏内容抽成变量：正常时住在右侧 Pane 里，互换后同一块进 PaneMain（主区）。
  const shellRightRail =
    level === 'l1'
      ? { open: agendaPaneOpen, toggle: () => togglePane('agenda-timeline') }
      : level !== null
        ? {
            open: fileBrowserOpen,
            toggle: () => {
              if (fileBrowserOpen) {
                setFileBrowserOpen(false)

                return
              }

              if (l2Tabs.length === 0) {
                setL2Tabs(['files'])
                setL2Active('files')
              } else if (l2Active === null) {
                setL2Active(l2Tabs[0])
              }

              setFileBrowserOpen(true)
            }
          }
        : undefined

  // 最外侧那张大卡（用户 10-31：「两个符号时刻在最靠窗口边的卡片上面」）：
  // 正常态=工作面侧卡；互换态=对话侧卡；聚焦时=只剩的那张。
  const workbenchSideOpen = level === 'l1' ? agendaPaneOpen : fileBrowserOpen
  const workbenchIsEdge = focusWorkbench || (!swapped && !focusChat && workbenchSideOpen)
  const chatIsEdge = !workbenchIsEdge

  const workbenchEdgeControls = {
    collapse: focusWorkbench
      ? { label: t.titlebar.exitFocus, onClick: () => setCardFocus(null) }
      : {
          icon: panesFlipped ? 'layout-sidebar-left' : 'layout-sidebar-right',
          label: t.titlebar.collapseWorkbench,
          onClick: () => shellRightRail?.toggle()
        },
    maximize: {
      label: focusWorkbench ? t.titlebar.restoreChat : t.titlebar.maximizeCard,
      onClick: () => setCardFocus(focusWorkbench ? null : 'workbench')
    }
  }

  const chatEdgeControls = {
    collapse: focusChat
      ? { label: t.titlebar.exitFocus, onClick: () => setCardFocus(null) }
      : swapped
        ? {
            icon: 'layout-sidebar-left',
            label: t.titlebar.workbenchBackToRight,
            onClick: () => setShellSwapped(false)
          }
        : {
            icon: 'layout-sidebar-right',
            label: t.titlebar.expandWorkbench,
            onClick: () => shellRightRail?.toggle()
          },
    maximize: {
      label: focusChat ? t.titlebar.restoreChat : t.titlebar.showChatOnly,
      onClick: () => setCardFocus(focusChat ? null : 'chat')
    }
  }

  const filesWorkbench = (
    <aside aria-label={t.workbench.aria} className={workbenchAsideClass}>
      <RightRailWorkbench
        activeTab={l2Active}
        availableTabs={l2TabDefs.filter(
          tab =>
            !l2Tabs.includes(tab.id) &&
            (tab.id !== 'review' || Boolean(agentCwd.trim())) &&
            (tab.id !== 'progress' || Boolean(activeAgentId))
        )}
        contents={{
          agenda: agendaTabContent,
          browser: <BrowserTabPanel labels={t.workbench.browser} />,
          files: workbenchFilesContent,
          progress: activeAgentId ? <AgentProgressPane agentId={activeAgentId} /> : null,
          review: workbenchReviewContent,
          terminal: isL1Workbench ? null : <TerminalPaneChrome />,
          ...fileTabContents
        }}
        dragHandleProps={shellCards && !focusWorkbench ? cardDragProps('rail') : undefined}
        edgeControls={workbenchIsEdge ? workbenchEdgeControls : undefined}
        labels={t.workbench}
        onTabChange={handleWorkbenchTabChange}
        onTabClose={handleWorkbenchTabClose}
        onTabOpen={handleWorkbenchTabOpen}
        tabs={l2OpenTabDefs}
      />
    </aside>
  )

  const fileBrowserPane = (
    <Pane
      defaultOpen={false}
      // WP-D: the file tree is the L2 workspace rail (§5.3). The workbench owns
      // the tabs here too; this column opens through Ctrl+J / review selection.
      disabled={!isL2}
      forceCollapsed={narrowViewport}
      hoverReveal={!shellCards}
      id="file-browser"
      key="file-browser"
      minWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      <div
        className="flex h-full min-h-0 flex-col"
        data-shell-card-drop={railDrop.over ? '' : undefined}
        {...railDrop.dropProps}
      >
        {filesWorkbench}
      </div>
    </Pane>
  )

  // Agent Capabilities rail（全局真源 + per-agent 开关，2026-10-19 用户裁定）：
  // 右栏这块 per-agent Capabilities 面板 2026-10-20 已按用户裁定拆掉：
  // 全局清单只住「设置 → Capabilities」，per-agent 的开关住左栏那一页
  // （同一个 Agent 自己的四张表），右栏不再有一份副本——它在宽屏下压着文件树，
  // 而且它列的本来就是全局清单，不是「这个 Agent 关掉了哪些」。


  // WP-D: the L1 secretary rail, rebuilt on the base Pane system. Same agenda
  // store and same four states the old console `RightRail` rendered — only the
  // chrome moved from a hand-rolled aside to the shell's right rail. This Pane
  // is L1-only; L2 gets the same `agendaTabContent` inside its own workbench.
  const agendaWorkbench = (
    <aside aria-label={t.workbench.aria} className={workbenchAsideClass}>
      <RightRailWorkbench
        activeTab={l1Active}
        availableTabs={l1TabDefs.filter(tab => !l1Tabs.includes(tab.id))}
        contents={{
          agenda: agendaTabContent,
          browser: <BrowserTabPanel labels={t.workbench.browser} />,
          files: null,
          terminal: isL1Workbench ? <TerminalPaneChrome /> : null,
          // 文件页签（日程附件点开的那种）。内容和 L2 那份是同一张 `fileTabContents`。
          ...fileTabContents
        }}
        dragHandleProps={shellCards && !focusWorkbench ? cardDragProps('rail') : undefined}
        edgeControls={workbenchIsEdge ? workbenchEdgeControls : undefined}
        labels={t.workbench}
        onTabChange={handleWorkbenchTabChange}
        onTabClose={handleWorkbenchTabClose}
        onTabOpen={handleWorkbenchTabOpen}
        tabs={l1OpenTabDefs}
      />
    </aside>
  )

  const agendaTimelinePane = (
    <Pane
      defaultOpen
      disabled={level !== 'l1'}
      divider
      forceCollapsed={narrowViewport}
      hoverReveal={!shellCards}
      id="agenda-timeline"
      key="agenda-timeline"
      minWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      <div
        className="flex h-full min-h-0 flex-col"
        data-shell-card-drop={railDrop.over ? '' : undefined}
        {...railDrop.dropProps}
      >
        {agendaWorkbench}
      </div>
    </Pane>
  )

  // 右栏开关：L1 是日程栏，L2 是工作台；对话卡抬头与顶栏重开共用这一份。
  const chatRoutes = (
    <Routes>
      {/* WP-A/E: `/` (L1), `/agent/:id` (L2) and `/console` all fall through
          to the single `*` secretary route below, so the center
          `ChatSurface` stays mounted across L1 ↔ L2 switches (only the
          left/right rail content changes). The legacy `/new` + `/:sessionId`
          pure-chat routes were removed (WP-E) — stray navigations to them
          land on `*` with a null level and bounce home. */}
      <Route
        element={
          <Suspense fallback={null}>
            <SkillsView setStatusbarItemGroup={setStatusbarItemGroup} />
          </Suspense>
        }
        path="skills"
      />
      <Route
        element={
          <Suspense fallback={null}>
            <MessagingView setStatusbarItemGroup={setStatusbarItemGroup} />
          </Suspense>
        }
        path="messaging"
      />
      <Route
        element={
          <Suspense fallback={null}>
            <ArtifactsView setStatusbarItemGroup={setStatusbarItemGroup} />
          </Suspense>
        }
        path="artifacts"
      />
      <Route
        element={
          <Suspense fallback={null}>
            <KnowledgeView />
          </Suspense>
        }
        path="knowledge"
      />
      <Route
        element={
          <Suspense fallback={null}>
            <DiaryView />
          </Suspense>
        }
        path="diary"
      />
      {/* Overlay routes: center stays empty; the card mounts via
          `overlays` from `useOverlayRouting`. Missing a stub here lets
          `*` match, null `chatScope` → Navigate home (open full board bounce). */}
      <Route element={null} path="agenda" />
      <Route element={null} path="cron" />
      <Route element={null} path="settings" />
      <Route element={null} path="command-center" />
      <Route element={null} path="agents" />
      <Route element={null} path="starmap" />
      <Route element={<LegacySessionRedirect />} path="sessions/:sessionId" />
      <Route element={<Navigate replace to={HOME_ROUTE} />} path="console" />
      <Route
        element={
          <SecretaryCenterPane
            cancel={stableCancel}
            chatScope={chatScope}
            composer={composer}
            modelMenuContent={modelMenuContent}
            edgeControls={chatIsEdge ? chatEdgeControls : undefined}
            submit={stableSubmit}
          />
        }
        path="*"
      />
    </Routes>
  )

  // 互换后：右栏内容进主区，对话进右侧窄卡。两张卡都带落点，拖另一张过来就换回。
  // 「只隐藏对话」（10-31）：工作面独占主区，对话不渲染，侧边栏照旧。
  const chatCard = (
    <div
      className="flex h-full min-h-0 flex-col"
      data-shell-card-drop={chatDrop.over ? '' : undefined}
      {...chatDrop.dropProps}
    >
      {chatRoutes}
    </div>
  )

  const railCard = (
    <div
      className="flex h-full min-h-0 flex-col"
      data-shell-card-drop={railDrop.over ? '' : undefined}
      {...railDrop.dropProps}
    >
      {level === 'l1' ? agendaWorkbench : filesWorkbench}
    </div>
  )

  // 互换后对话卡落到右侧栏原来的位置，宽度与右栏同量级但给到能读宽度、可拖。
  const chatPane = (
    <Pane defaultOpen id="chat-pane" key="chat-pane" minWidth={360} resizable side="right" width="42rem">
      {chatCard}
    </Pane>
  )

  // PaneShell 只收**直接子元素**（Children.forEach 不钻 Fragment）：这一组必须平铺成数组，
  // 用 <>…</> 包起来的话 Pane 收不到槽位、就整块不渲染（10-31 自测：右栏与对话窄卡都消失）。
  const shellPanes = focusChat
    ? [<PaneMain key="main">{chatCard}</PaneMain>]
    : focusWorkbench
      ? [<PaneMain key="main">{railCard}</PaneMain>]
      : swapped
        ? [<PaneMain key="main">{railCard}</PaneMain>, chatPane]
        : [
            <PaneMain key="main">{chatCard}</PaneMain>,
            panesFlipped ? fileBrowserPane : null,
            previewPane,
            agendaTimelinePane,
            panesFlipped ? null : fileBrowserPane
          ]

  // 左栏也是一条 Pane：换边后它必须排在同侧最后（最外一列），否则会掉进对话和工作面中间
  // ——10-31 第二张截图那个 bug。正常态它排最前。
  const sidebarPane = !isSecondaryWindow() ? (
    <Pane
      disabled={!sidebarPaneOpen}
      forceWidth={sidebarOpen ? undefined : `${SIDEBAR_COLLAPSED_WIDTH}px`}
      id="chat-sidebar"
      key="chat-sidebar"
      minWidth={SIDEBAR_DEFAULT_WIDTH}
      maxWidth={SIDEBAR_MAX_WIDTH}
      resizable={sidebarOpen}
      side={sidebarSide}
      width={`${SIDEBAR_DEFAULT_WIDTH}px`}
    >
      {sidebar}
    </Pane>
  ) : null

  const orderedPanes = panesFlipped ? [...shellPanes, sidebarPane] : [sidebarPane, ...shellPanes]

  return (
    <AppShell
      leftStatusbarItems={leftStatusbarItems}
      leftTitlebarTools={titlebarToolGroups.flat.left}
      mainOverlays={mainOverlays}
      onOpenSettings={openSettings}
      overlays={overlays}
      previewPaneOpen={!swapped && secretaryShellOpen && previewRailContent}
      rightRail={swapped ? undefined : shellRightRail}
      statusbarItems={statusbarItems}
      titlebarTools={titlebarToolGroups.flat.right}
    >
      {/* 顺序即列序：正常态 sidebar | main | 右栏卡；换边后整条镜像，侧边栏排同侧最后 */}
      {orderedPanes}
    </AppShell>
  )
}

