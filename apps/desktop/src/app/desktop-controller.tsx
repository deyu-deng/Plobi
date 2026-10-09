import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { PanelList, PanelListRow, PanelSectionLabel } from '@/app/overlays/panel'
import { BootFailureOverlay } from '@/components/boot-failure-overlay'
import { DesktopInstallOverlay } from '@/components/desktop-install-overlay'
import { GatewayConnectingOverlay } from '@/components/gateway-connecting-overlay'
import { DesktopOnboardingOverlay } from '@/components/onboarding'
import { PageLoader } from '@/components/page-loader'
import { Pane, PaneMain } from '@/components/pane-shell'
import { RemoteDisplayBanner } from '@/components/remote-display-banner'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useI18n } from '@/i18n'
import { isFocusWithin } from '@/lib/keybinds/combo'
import { cn } from '@/lib/utils'
import { $agendaError, $agendaEvents, $agendaLoading } from '@/store/agenda'
import { useSkinCommand } from '@/themes/use-skin-command'

import { bulkDeleteSessions, getSessionMessages, listAllProfileSessions, type SessionMessage, triggerCronJob } from '../plobi'
import { type ChatMessage, chatMessageText, preserveLocalAssistantErrors, toChatMessages } from '../lib/chat-messages'
import { storedSessionIdForNotification } from '../lib/session-ids'
import { isMessagingSource, MESSAGING_SESSION_SOURCE_IDS } from '../lib/session-source'
import { latestSessionTodos } from '../lib/todos'
import { setCronFocusJobId } from '../store/cron'
import {
  $fileBrowserOpen,
  $panesFlipped,
  $pinnedSessionIds,
  FILE_BROWSER_DEFAULT_WIDTH,
  FILE_BROWSER_MAX_WIDTH,
  FILE_BROWSER_MIN_WIDTH,
  pinSession,
  PREVIEW_PANE_ID,
  restoreWorktree,
  setSidebarOverlayMounted,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  unpinSession
} from '../store/layout'
import { respondToApprovalAction } from '../store/native-notifications'
import { $paneOpen, setPaneOpen, togglePane } from '../store/panes'
import { setPetActivity } from '../store/pet'
import { setPetScale } from '../store/pet-gallery'
import {
  setPetOverlayOpenAppHandler,
  setPetOverlayScaleHandler,
  setPetOverlaySubmitHandler
} from '../store/pet-overlay'
import { $filePreviewTarget, $previewTarget, closeActiveRightRailTab } from '../store/preview'
import { $activeGatewayProfile, $freshSessionRequest, $profiles, $profileScope, ensureGatewayProfile, normalizeProfileKey, refreshActiveProfile, refreshProfiles } from '../store/profile'
import { $startWorkSessionRequest, followActiveSessionCwd, resolveNewSessionCwd } from '../store/projects'
import { $reviewOpen, REVIEW_PANE_ID } from '../store/review'
import {
  $activeSessionId,
  $attentionSessionIds,
  $currentCwd,
  $freshDraftReady,
  $gatewayState,
  $messages,
  $messagingSessions,
  $resumeExhaustedSessionId,
  $resumeFailedSessionId,
  $selectedStoredSessionId,
  $sessions,
  getRememberedSessionId,
  sessionPinId,
  setActiveSessionId,
  setAwaitingResponse,
  setBusy,
  setCurrentBranch,
  setCurrentCwd,
  setCurrentModel,
  setCurrentProvider,
  setFreshDraftReady,
  setMessages,
  setRememberedSessionId,
  setSelectedStoredSessionId
} from '../store/session'
import { onSessionsChanged } from '../store/session-sync'
import { clearSessionTodos, setSessionTodos, todosForHydration } from '../store/todos'
import { openUpdatesWindow, startUpdatePoller, stopUpdatePoller } from '../store/updates'
import { isSecondaryWindow } from '../store/windows'

import { requestComposerFocus, requestComposerInsert } from './chat/composer/focus'
import { useComposerActions } from './chat/hooks/use-composer-actions'
import {
  ChatPreviewRail,
  PREVIEW_RAIL_MAX_WIDTH,
  PREVIEW_RAIL_MIN_WIDTH,
  PREVIEW_RAIL_PANE_WIDTH
} from './chat/right-rail'
import { ChatSidebar } from './chat/sidebar'
import { CommandPalette } from './command-palette'
import { ChatSurface, type ChatSurfaceProps } from './console/chat/chat-surface'
import { agentScope, type ChatScope, L1_SCOPE } from './console/chat/scope'
import { AddEventForm } from './console/right/add-event'
import { Timeline } from './console/right/timeline'
import {
  $agendaAnchors,
  $agendaAvoidWindows,
  $agendaPlans,
  $agendaRhythmOff,
  refreshAgendaTimeline,
  useAgendaTimeline
} from './console/right/use-agenda-timeline'
import { getAgentOverview } from './console/api'
import { $consoleAgents, refreshConsoleAgents } from './console/store/agents'
import {
  bindL1GatewayIfMasterExists,
  collectL1BypassSessionIds,
  findKnownOverviewSession,
  forgetL1MainSession,
  pickL1MainSession,
  readL1MainSessionId,
  resolveL1HomeProfile,
  restartsMouthInPlace,
  secretaryShellLabel,
  writeL1MainSessionId
} from './desktop-controller-utils'
import { useGatewayBoot } from './gateway/hooks/use-gateway-boot'
import { useGatewayRequest } from './gateway/hooks/use-gateway-request'
import { useKeybinds } from './hooks/use-keybinds'
import { SIDEBAR_COLLAPSE_MEDIA_QUERY } from './layout-constants'
import { ModelPickerOverlay } from './model-picker-overlay'
import { ModelVisibilityOverlay } from './model-visibility-overlay'
import { PetGenerateOverlay } from './pet-generate/pet-generate-overlay'
import { RightSidebarPane, RightSidebarSectionHeader } from './right-sidebar'
import { FileActionDialogs } from './right-sidebar/file-actions'
import { RemoteFolderPicker } from './right-sidebar/files/remote-picker'
import { ReviewPane } from './right-sidebar/review'
import { $terminalTakeover } from './right-sidebar/store'
import { TerminalPaneChrome } from './right-sidebar/terminal/chrome'
import { PersistentTerminal } from './right-sidebar/terminal/persistent'
import { closeActiveTerminal } from './right-sidebar/terminal/terminals'
import {
  AGENDA_ROUTE,
  CRON_ROUTE,
  HOME_ROUTE,
  isShellPageView,
  NEW_CHAT_ROUTE,
  routeSessionId,
  sessionRoute,
  SETTINGS_ROUTE,
  shellLevelForPath
} from './routes'
import { SessionPickerOverlay } from './session-picker-overlay'
import { SessionSwitcher } from './session-switcher'
import { useContextSuggestions } from './session/hooks/use-context-suggestions'
import { useCwdActions } from './session/hooks/use-cwd-actions'
import { usePlobiConfig } from './session/hooks/use-plobi-config'
import { useMessageStream } from './session/hooks/use-message-stream'
import { useModelControls } from './session/hooks/use-model-controls'
import { usePreviewRouting } from './session/hooks/use-preview-routing'
import { usePromptActions } from './session/hooks/use-prompt-actions'
import { useRouteResume } from './session/hooks/use-route-resume'
import { useSessionActions } from './session/hooks/use-session-actions'
import { useSessionListActions } from './session/hooks/use-session-list-actions'
import { useSessionStateCache } from './session/hooks/use-session-state-cache'
import { AppShell } from './shell/app-shell'
import { useOverlayRouting } from './shell/hooks/use-overlay-routing'
import { useStatusSnapshot } from './shell/hooks/use-status-snapshot'
import { useStatusbarItems } from './shell/hooks/use-statusbar-items'
import { ModelMenuPanel } from './shell/model-menu-panel'
import { SidebarPanelLabel } from './shell/sidebar-label'
import type { StatusbarItem } from './shell/statusbar-controls'
import type { TitlebarTool } from './shell/titlebar-controls'
import { useGroupRegistry } from './shell/use-group-registry'
import { UpdatesOverlay } from './updates-overlay'

const AgendaView = lazy(async () => ({ default: (await import('./agenda')).AgendaView }))
const AgentsView = lazy(async () => ({ default: (await import('./agents')).AgentsView }))
const ArtifactsView = lazy(async () => ({ default: (await import('./artifacts')).ArtifactsView }))
const CommandCenterView = lazy(async () => ({ default: (await import('./command-center')).CommandCenterView }))
const CronView = lazy(async () => ({ default: (await import('./cron')).CronView }))
const StarmapView = lazy(async () => ({ default: (await import('./starmap')).StarmapView }))
const MessagingView = lazy(async () => ({ default: (await import('./messaging')).MessagingView }))
const ProfilesView = lazy(async () => ({ default: (await import('./profiles')).ProfilesView }))
const SettingsView = lazy(async () => ({ default: (await import('./settings')).SettingsView }))
const SkillsView = lazy(async () => ({ default: (await import('./skills')).SkillsView }))

// Latest cron-job sessions surfaced in the collapsed "Cron jobs" section. The
// Cron sessions are written by a background scheduler tick (the desktop
// backend), so no user action signals the UI. Poll the bounded cron list on
// this cadence while the app is open + visible so new runs surface promptly
// instead of waiting for the next user-triggered refreshSessions().
const CRON_POLL_INTERVAL_MS = 30_000
// Messaging-platform turns are written by the background gateway (WeChat,
// Telegram, Discord, …), not the desktop websocket that drives local chats.
// Poll the bounded messaging slice while visible so inbound platform traffic
// appears without requiring a manual refresh or route change.
const MESSAGING_POLL_INTERVAL_MS = 10_000
const ACTIVE_MESSAGING_SESSION_POLL_INTERVAL_MS = 5_000

function sessionMatchesStoredId(session: { id: string; _lineage_root_id?: null | string }, id: string): boolean {
  return session.id === id || session._lineage_root_id === id
}

function hashString(hash: number, value: string): number {
  let next = hash

  for (let i = 0; i < value.length; i++) {
    next ^= value.charCodeAt(i)
    next = Math.imul(next, 16777619)
  }

  return next >>> 0
}

function sessionMessagesSignature(messages: SessionMessage[]): string {
  let hash = 2166136261

  for (const m of messages) {
    hash = hashString(hash, m.role)
    hash = hashString(hash, String(m.timestamp ?? ''))
    hash = hashString(hash, typeof m.content === 'string' ? m.content : (JSON.stringify(m.content) ?? ''))
  }

  return `${messages.length}:${hash}`
}

export function DesktopController() {
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useI18n()

  const busyRef = useRef(false)
  const creatingSessionRef = useRef(false)
  const messagingTranscriptSignatureRef = useRef(new Map<string, string>())

  const gatewayState = useStore($gatewayState)
  const activeSessionId = useStore($activeSessionId)
  const currentCwd = useStore($currentCwd)
  const freshDraftReady = useStore($freshDraftReady)
  const resumeFailedSessionId = useStore($resumeFailedSessionId)
  const resumeExhaustedSessionId = useStore($resumeExhaustedSessionId)
  const filePreviewTarget = useStore($filePreviewTarget)
  const previewTarget = useStore($previewTarget)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const messagingSessions = useStore($messagingSessions)
  const terminalTakeover = useStore($terminalTakeover)
  const reviewOpen = useStore($reviewOpen)
  const fileBrowserOpen = useStore($fileBrowserOpen)
  const previewPaneOpen = useStore($paneOpen(PREVIEW_PANE_ID))
  const agendaPaneOpen = useStore($paneOpen('agenda-timeline'))
  const panesFlipped = useStore($panesFlipped)
  const profileScope = useStore($profileScope)
  // WP-D: the L1 agenda rail reads the same single agenda store the full-screen
  // board renders; the controller subscribes so the base Pane can host it.
  const agendaEvents = useStore($agendaEvents)
  const agendaAnchors = useStore($agendaAnchors)
  const agendaPlans = useStore($agendaPlans)
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
    profilesOpen,
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
  const previewRailContent =
    Boolean(previewTarget) || (isL2 && Boolean(filePreviewTarget))

  // The L1 rail owns the agenda poll; every other route leaves the store cold
  // (`enabled: false` → no seed, no 8s interval).
  useAgendaTimeline(level === 'l1')
  const [agendaAdding, setAgendaAdding] = useState(false)

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

  // WP-D: the terminal takeover applies to the whole secretary shell, not just
  // the legacy chat routes.
  const terminalSidebarOpen = conversationShellOpen && terminalTakeover

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

  const toggleSelectedPin = useCallback(() => {
    const sessionId = $selectedStoredSessionId.get()

    if (!sessionId) {
      return
    }

    // Pin on the durable lineage-root id so the pin survives auto-compression.
    const session = $sessions.get().find(s => s.id === sessionId || s._lineage_root_id === sessionId)
    const pinId = session ? sessionPinId(session) : sessionId

    if ($pinnedSessionIds.get().includes(pinId)) {
      unpinSession(pinId)
    } else {
      pinSession(pinId)
    }
  }, [])

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

  const hydrateFromStoredSession = useCallback(
    async (
      attempts = 1,
      storedSessionId = selectedStoredSessionIdRef.current,
      runtimeSessionId = activeSessionIdRef.current
    ) => {
      if (!storedSessionId || !runtimeSessionId) {
        return
      }

      const storedProfile = $sessions
        .get()
        .find(session => session.id === storedSessionId || session._lineage_root_id === storedSessionId)?.profile

      for (let index = 0; index < Math.max(1, attempts); index += 1) {
        try {
          const latest = await getSessionMessages(storedSessionId, storedProfile)
          const messages = toChatMessages(latest.messages)
          updateSessionState(
            runtimeSessionId,
            state => ({
              ...state,
              messages: preserveLocalAssistantErrors(messages, state.messages)
            }),
            storedSessionId
          )

          // Rehydration runs *after* a turn completes, so an "active" stored
          // list (last `todo` still pending/in_progress) means the turn ended
          // without a final update — it's stale, not in-flight. Re-seeding it
          // would re-pin "Tasks N/M" above the composer and undo the turn-end
          // clear (and survive restarts, since it's read back from history).
          // todosForHydration restores only a *finished* list (its short linger
          // shows the last checkmark); anything still active is dropped.
          const restored = todosForHydration(latestSessionTodos(messages))

          if (restored) {
            setSessionTodos(runtimeSessionId, restored)
          } else {
            clearSessionTodos(runtimeSessionId)
          }

          return
        } catch {
          // Best-effort fallback when live stream payloads are empty.
        }

        if (index < attempts - 1) {
          await new Promise(resolve => window.setTimeout(resolve, 250))
        }
      }
    },
    [activeSessionIdRef, selectedStoredSessionIdRef, updateSessionState]
  )

  const refreshActiveMessagingTranscript = useCallback(async () => {
    const storedSessionId = selectedStoredSessionIdRef.current
    const runtimeSessionId = activeSessionIdRef.current

    if (!storedSessionId || !runtimeSessionId || busyRef.current) {
      return
    }

    const stored = $messagingSessions.get().find(s => sessionMatchesStoredId(s, storedSessionId))

    if (!stored || !isMessagingSource(stored.source)) {
      return
    }

    try {
      const latest = await getSessionMessages(storedSessionId, stored.profile)
      const signatureKey = `${stored.profile ?? 'default'}:${storedSessionId}`
      const sig = sessionMessagesSignature(latest.messages)

      if (messagingTranscriptSignatureRef.current.get(signatureKey) === sig) {
        return
      }

      messagingTranscriptSignatureRef.current.set(signatureKey, sig)
      const messages = toChatMessages(latest.messages)

      updateSessionState(
        runtimeSessionId,
        state => ({ ...state, messages: preserveLocalAssistantErrors(messages, state.messages) }),
        storedSessionId
      )
    } catch {
      // Non-fatal: next poll or manual refresh can hydrate.
    }
  }, [activeSessionIdRef, busyRef, selectedStoredSessionIdRef, updateSessionState])

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

  // The "nothing to resume yet" state of a secretary shell: an empty draft under
  // the current route. Shared by the level-keyed bind below and 「重新开始」 so a
  // reset lands exactly where a fresh shell lands. The refs — not just the atoms
  // — are what the submit pipeline pins its session context to; leaving them on
  // the previous context made the next message resume a stale id and abort with
  // "context drifted".
  const prepareFreshDraftInPlace = useCallback(() => {
    // `busyRef` gates the composer's send and no atom write clears it: left armed
    // here, the empty draft would stay unsendable until the retired run reported.
    busyRef.current = false
    setBusy(false)
    setAwaitingResponse(false)
    setActiveSessionId(null)
    setSelectedStoredSessionId(null)
    activeSessionIdRef.current = null
    selectedStoredSessionIdRef.current = null
    setMessages([])
    setFreshDraftReady(true)
    setCurrentCwd(resolveNewSessionCwd())
    setCurrentBranch('')
  }, [activeSessionIdRef, busyRef, selectedStoredSessionIdRef])

  // 「重新开始」 for every path that asks for it (⌘N, the palette, `/new` and its
  // `/reset` alias, a profile switch): see `restartsMouthInPlace` for why the
  // secretary shell resets where it stands instead of routing `/new`.
  const startFreshConversation = useCallback(() => {
    if (!restartsMouthInPlace(level)) {
      startFreshSessionDraft()

      return
    }

    if (level === 'l1') {
      forgetL1MainSession(normalizeProfileKey($activeGatewayProfile.get() || 'default'))
      prepareFreshDraftInPlace()

      return
    }

    // An L2 mouth keeps its agent's bound folder; the draft's project
    // re-resolve would point the file tree at the last project visited.
    const agentCwd = $currentCwd.get()

    prepareFreshDraftInPlace()
    setCurrentCwd(agentCwd)
  }, [level, prepareFreshDraftInPlace, startFreshSessionDraft])

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

  // A profile switch/create drops to a fresh new-session draft so the previously
  // open session doesn't bleed across contexts. Skip the initial value.
  const freshSessionRequest = useStore($freshSessionRequest)
  const lastFreshRef = useRef(freshSessionRequest)

  useEffect(() => {
    if (freshSessionRequest === lastFreshRef.current) {
      return
    }

    lastFreshRef.current = freshSessionRequest
    startFreshConversation()
  }, [freshSessionRequest, startFreshConversation])

  // Swapping the live gateway to another profile must re-pull that profile's
  // global model + active-profile pill. Both are nanostores, so the blanket
  // invalidateQueries() the profile store fires on swap doesn't touch them —
  // without this the statusbar keeps showing the previous profile's model
  // (the "forgets the LLM setting" report). gatewayState stays 'open' across a
  // swap (background sockets persist), so the open→open effect won't re-run.
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const lastGatewayProfileRef = useRef(activeGatewayProfile)

  useEffect(() => {
    if (activeGatewayProfile === lastGatewayProfileRef.current) {
      return
    }

    lastGatewayProfileRef.current = activeGatewayProfile
    // Force: the new profile has its own default, so reseed even if the composer
    // already shows the previous profile's model.
    void refreshCurrentModel(true)
    void refreshActiveProfile()
  }, [activeGatewayProfile, refreshCurrentModel])

  // 裁定 44:「一个 Agent 一张嘴」— the composer's model/provider stickiness is an
  // attribute of the person you're talking to, not of this window. `chatScope` is
  // memoized on the route, so this runs on mount and on every L1 ↔ L2 / L2 ↔ L2
  // switch. `refreshCurrentModel` first re-points the persistence cell at that
  // scope (and re-hydrates the pill from it, so leaving a project restores the
  // secretary's own last pick), then: `force` for an agent = that agent's cell
  // outranks the profile default; `false` for the flat scope = fill only when
  // empty, so boot and profile-swap behaviour is unchanged.
  useEffect(() => {
    void refreshCurrentModel(chatScope?.kind === 'agent')
  }, [chatScope, refreshCurrentModel])

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

  // Clear a failed turn's red error banner from the transcript. Errors are
  // renderer-local state (never persisted), so dismissing is purely a view +
  // session-cache edit. A message that errored before emitting any visible
  // text is a bare error placeholder → drop it entirely; one that streamed
  // partial output then failed keeps its content and just sheds the error.
  // Both the per-runtime cache AND the live $messages view must be updated:
  // `preserveLocalAssistantErrors` re-grafts any still-errored message it
  // finds in the view onto the next session.info flush, so clearing only the
  // cache would let the heartbeat resurrect the banner.
  const dismissError = useCallback(
    (messageId: string) => {
      const runtimeSessionId = activeSessionIdRef.current

      if (!runtimeSessionId) {
        return
      }

      const clearErrorIn = (messages: ChatMessage[]): ChatMessage[] =>
        messages.flatMap(message => {
          if (message.id !== messageId || !message.error) {
            return [message]
          }

          if (!chatMessageText(message).trim() && !message.parts.some(part => part.type !== 'text')) {
            return []
          }

          return [{ ...message, error: undefined, pending: false }]
        })

      // View first: the flush below reads $messages as the "current" baseline
      // for error preservation, so the banner must be gone from it before the
      // cache update triggers a re-sync.
      setMessages(clearErrorIn($messages.get()))

      updateSessionState(runtimeSessionId, state => ({
        ...state,
        messages: clearErrorIn(state.messages)
      }))
    },
    [activeSessionIdRef, updateSessionState]
  )

  const startSessionInWorkspace = useCallback(
    (path: null | string) => {
      startFreshSessionDraft()

      // A worktree lane carries its own path; the trunk "+" can be path-less (the
      // main checkout is implicit), so fall back to the active project's root
      // instead of no-op'ing on null — that was "+ on main does nothing".
      const target = path?.trim() || resolveNewSessionCwd()

      if (!target) {
        return
      }

      // The next message creates the backend session in $currentCwd, so seed
      // it (and the branch) from the workspace the user clicked the + on.
      setCurrentCwd(target)
      void requestGateway<{ branch?: string; cwd?: string }>('config.get', { key: 'project', cwd: target })
        .then(info => {
          const resolved = info.cwd || target

          setCurrentCwd(resolved)
          setCurrentBranch(info.branch || '')

          // An EXPLICIT target (a worktree/lane path — e.g. just-created via
          // "convert a branch" / "new worktree") drills the sidebar into that
          // project so the new lane is visible at once. Without this, a brand-new
          // worktree session is invisible from the all-projects overview (the
          // live overlay skips `.worktrees` rows, and the session.info cwd-follow
          // only fires on a same-session move, not a fresh session). The
          // path-less trunk "+" keeps the current scope untouched.
          if (path?.trim()) {
            restoreWorktree(resolved)
            void followActiveSessionCwd(resolved)
          }
        })
        .catch(() => undefined)
    },
    [requestGateway, startFreshSessionDraft]
  )

  // Composer "branch off into a new worktree": the composer already created the
  // worktree and cleared its draft; open a fresh session anchored to that tree,
  // then prefill the task that kicked it off. startSessionInWorkspace owns the
  // reset+cwd seed (it runs startFreshSessionDraft, which would otherwise stomp
  // the cwd back to the default), so the prefill is dispatched right after — its
  // deferred event lands once the fresh composer has remounted and rebound.
  const startWorkSessionRequest = useStore($startWorkSessionRequest)
  const lastStartWorkTokenRef = useRef(startWorkSessionRequest?.token ?? 0)

  useEffect(() => {
    if (!startWorkSessionRequest || startWorkSessionRequest.token === lastStartWorkTokenRef.current) {
      return
    }

    lastStartWorkTokenRef.current = startWorkSessionRequest.token
    startSessionInWorkspace(startWorkSessionRequest.path)

    if (startWorkSessionRequest.draft) {
      requestComposerInsert(startWorkSessionRequest.draft, { target: 'main' })
    }
  }, [startSessionInWorkspace, startWorkSessionRequest])

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
    desktopQuotaStream: {
      appendAssistantDelta,
      appendReasoningDelta,
      completeAssistantMessage,
      failAssistantMessage
    },
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
    (text: string, options?: { attachments?: import('@/store/composer').ComposerAttachment[]; fromQueue?: boolean }) =>
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
            .map(agent => normalizeProfileKey((agent.profile || agent.id).trim()))
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

        const existing = pickL1MainSession(
          $sessions.get(),
          profileForSessions,
          readL1MainSessionId(profileForSessions)
        )

        if (existing) {
          writeL1MainSessionId(profileForSessions, existing.id)
          await resumeSessionRef.current(existing.id)
        } else {
          prepareFreshDraftInPlace()
        }

        // Drop every other L1 local chat — one conversation only, no bypass drawer.
        try {
          const agentProfiles = $consoleAgents
            .get()
            .map(agent => (agent.profile ?? '').trim())
            .filter(Boolean)
          const listed = await listAllProfileSessions(500, 0, 'exclude', 'recent', profileForSessions, {
            excludeSources: ['cron', 'subagent', 'tool', ...MESSAGING_SESSION_SOURCE_IDS]
          })
          const bypassIds = collectL1BypassSessionIds(listed.sessions, {
            agentProfiles,
            keepId: existing?.id ?? null,
            profile: profileForSessions
          })

          for (let i = 0; i < bypassIds.length; i += 500) {
            const chunk = bypassIds.slice(i, i + 500)

            if (chunk.length) {
              await bulkDeleteSessions(chunk, profileForSessions)
            }
          }

          if (bypassIds.length) {
            await refreshSessionsRef.current().catch(() => undefined)
          }
        } catch {
          // Non-fatal: UI already hides bypass lists; purge can retry next L1 enter.
        }

        if (cancelled) {
          return
        }

        l1GatewayProfileRef.current = profileForSessions

        return
      }

      const agentId = level.l2
      let profile =
        $consoleAgents.get().find(agent => agent.id === agentId)?.profile?.trim() || agentId
      let sessionId = ''
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
      }

      // Mount the agent's bound folder for the right-rail file tree. Empty
      // (butler-type: no project) clears instead of inheriting L1 / the
      // previous agent's cwd; a nonexistent path just renders an empty tree
      // and never blocks the center chat.
      const mountAgentCwd = () => {
        if (agentProjectPath === null) {
          return
        }

        setCurrentCwd(agentProjectPath)
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
      // Refresh L1 home from the profile we're leaving when it is still an L1
      // profile; keep the remembered home when hopping L2→L2 (agent→agent).
      l1GatewayProfileRef.current = resolveL1HomeProfile({
        activeProfile: previousProfile,
        agentProfiles: $consoleAgents
          .get()
          .map(agent => normalizeProfileKey((agent.profile || agent.id).trim()))
          .filter(Boolean),
        rememberedHome: l1GatewayProfileRef.current
      })

      try {
        await ensureGatewayProfile(profile)
      } catch {
        await ensureGatewayProfile(previousProfile).catch(() => undefined)
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
        await ensureGatewayProfile(previousProfile).catch(() => undefined)
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
    })().catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [level, gatewayState, prepareFreshDraftInPlace])

  useEffect(() => {
    if (isSecondaryWindow()) {
      return
    }

    setPetOverlaySubmitHandler(text => void submitTextRef.current(text))
    // Alt+wheel resize from the popped-out pet — persist it through this
    // window's gateway (the overlay has none) so it survives restart.
    setPetOverlayScaleHandler(scale => setPetScale(requestGatewayRef.current, scale))
    // Mail icon: $sessions is ordered most-recent-first; the pet is global (not
    // per session) so "most recent" is the right target. main.ts already raised
    // the window before forwarding this.
    setPetOverlayOpenAppHandler(() => {
      const recent = $sessions.get()[0]

      if (recent?.id) {
        void resumeSessionRef.current(recent.id)
      }
    })

    return () => {
      setPetOverlaySubmitHandler(null)
      setPetOverlayOpenAppHandler(null)
      setPetOverlayScaleHandler(null)
    }
  }, [])

  // Mirror "a session is blocked on the user" (clarify/approval) into the pet's
  // awaitingInput flag so it shows the `waiting` pose. Lives on $petActivity so
  // it rides the same atom the pop-out overlay mirrors — no session list needed
  // there. Every window keeps its own in-window pet in sync.
  useEffect(() => {
    const sync = () => setPetActivity({ awaitingInput: $attentionSessionIds.get().length > 0 })

    sync()

    return $attentionSessionIds.listen(sync)
  }, [])

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

  useEffect(() => {
    if (gatewayState === 'open') {
      void refreshCurrentModel()
      void refreshActiveProfile()
      void refreshSessions().catch(() => undefined)
    }
  }, [gatewayState, refreshCurrentModel, refreshSessions])

  // Keep the cron jobs section live without a user action: the scheduler ticks
  // in the background (advancing next-run/state and creating runs), so poll the
  // job list on an interval (and on tab re-focus) while connected.
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    const tick = () => {
      if (document.visibilityState === 'visible') {
        void refreshCronJobs()
      }
    }

    const intervalId = window.setInterval(tick, CRON_POLL_INTERVAL_MS)
    document.addEventListener('visibilitychange', tick)

    return () => {
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [gatewayState, refreshCronJobs])

  // Keep messaging-platform session lists live: inbound Telegram/WeChat/Discord
  // turns are written by the gateway, not the desktop websocket, so they won't
  // appear without polling.
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    const tick = () => {
      if (document.visibilityState === 'visible') {
        void refreshMessagingSessions()
      }
    }

    const intervalId = window.setInterval(tick, MESSAGING_POLL_INTERVAL_MS)
    document.addEventListener('visibilitychange', tick)

    return () => {
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [gatewayState, refreshMessagingSessions])

  // Only the open messaging transcript needs a poll — local chats are already
  // live over the websocket, so arming a timer for them would just no-op every
  // tick. Gate on the active session actually being a messaging source.
  const activeIsMessaging =
    !!selectedStoredSessionId &&
    isMessagingSource(messagingSessions.find(s => sessionMatchesStoredId(s, selectedStoredSessionId))?.source)

  // Keep the currently-viewed messaging transcript live.
  useEffect(() => {
    if (gatewayState !== 'open' || !activeIsMessaging) {
      return
    }

    const tick = () => {
      if (document.visibilityState === 'visible') {
        void refreshActiveMessagingTranscript()
      }
    }

    const intervalId = window.setInterval(tick, ACTIVE_MESSAGING_SESSION_POLL_INTERVAL_MS)
    document.addEventListener('visibilitychange', tick)
    tick()

    return () => {
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [activeIsMessaging, gatewayState, refreshActiveMessagingTranscript])

  useEffect(() => {
    if (gatewayState === 'open' && !activeSessionId && freshDraftReady) {
      void refreshCurrentModel()
      void refreshPlobiConfig()
    }
  }, [activeSessionId, freshDraftReady, gatewayState, refreshCurrentModel, refreshPlobiConfig])

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
    chatOpen,
    commandCenterOpen,
    extraLeftItems: statusbarItemGroups.flat.left,
    extraRightItems: statusbarItemGroups.flat.right,
    gatewayState,
    inferenceStatus,
    openAgents,
    freshDraftReady,
    openCommandCenterSection,
    requestGateway,
    shellOpen: conversationShellOpen,
    statusSnapshot,
    toggleCommandCenter
  })

  const sidebar = (
    <ChatSidebar
      currentView={currentView}
      level={level}
      onArchiveSession={sessionId => void archiveSession(sessionId)}
      onBranchSession={sessionId => void branchStoredSession(sessionId)}
      onDeleteSession={sessionId => void removeSession(sessionId)}
      onLoadMoreMessaging={loadMoreMessagingForPlatform}
      onLoadMoreProfileSessions={loadMoreSessionsForProfile}
      onLoadMoreSessions={loadMoreSessions}
      onManageCronJob={jobId => {
        setCronFocusJobId(jobId)
        navigate(CRON_ROUTE)
      }}
      onNavigate={selectSidebarItem}
      onNewSessionInWorkspace={startSessionInWorkspace}
      onResumeSession={sessionId => {
        // Secretary shells keep the URL (`/` / `/agent/:id`) and resume in place.
        // Navigating to `/:sessionId` nulls shellLevel and bounces home (WP-E).
        if (level) {
          void resumeSessionRef.current(sessionId)

          return
        }

        navigate(sessionRoute(sessionId))
      }}
      onTriggerCronJob={jobId => {
        void triggerCronJob(jobId)
          .then(() => refreshCronJobs())
          .catch(() => undefined)
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
      <GatewayConnectingOverlay />
      <BootFailureOverlay />
      <CommandPalette />
      <PetGenerateOverlay />
      <SessionSwitcher />
      <FileActionDialogs />
      <RemoteFolderPicker />

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

      {profilesOpen && (
        <Suspense fallback={null}>
          <ProfilesView onClose={closeOverlayToPreviousRoute} />
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

  // Other sidebars docked as real columns on the terminal's rail. Force-collapsed
  // hover-reveal overlays (narrow window) don't take a column, so they don't count.
  const railColumnOpen =
    (secretaryShellOpen && previewRailContent && previewPaneOpen) ||
    (isL2 && !narrowViewport && fileBrowserOpen) ||
    (isL2 && Boolean(currentCwd.trim()) && !narrowViewport && reviewOpen) ||
    (level === 'l1' && !narrowViewport && agendaPaneOpen)

  // Once the terminal would share its rail with another sidebar, drop it to a
  // full-width row beneath them rather than cramming in one more skinny column.
  const terminalAsRow = terminalSidebarOpen && railColumnOpen

  const previewPane = (
    <Pane
      disabled={!secretaryShellOpen || !previewRailContent}
      id={PREVIEW_PANE_ID}
      key="preview"
      maxWidth={PREVIEW_RAIL_MAX_WIDTH}
      minWidth={PREVIEW_RAIL_MIN_WIDTH}
      resizable
      side={railSide}
      width={PREVIEW_RAIL_PANE_WIDTH}
    >
      {secretaryShellOpen && previewRailContent ? (
        <ChatPreviewRail onRestartServer={restartPreviewServer} setTitlebarToolGroup={setTitlebarToolGroup} />
      ) : null}
    </Pane>
  )

  const fileBrowserPane = (
    <Pane
      defaultOpen={false}
      // WP-D: the file tree is the L2 workspace rail (§5.3) — the secretary L1
      // route must never surface a workspace switcher. Pure-chat `chatOpen` is
      // gone (WP-E); only L2 enables this column.
      disabled={!isL2}
      forceCollapsed={narrowViewport}
      hoverReveal
      id="file-browser"
      key="file-browser"
      maxWidth={FILE_BROWSER_MAX_WIDTH}
      minWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      {/* Key on the project (cwd) so switching projects unmounts the old tree and
          mounts a fresh one straight into its skeleton — no stale-then-blip. */}
      <RightSidebarPane
        key={currentCwd || 'no-cwd'}
        onActivateFile={path => composer.insertContextPathInlineRef(path)}
        onActivateFolder={path => composer.insertContextPathInlineRef(path, true)}
      />
    </Pane>
  )

  const reviewPane = (
    <Pane
      defaultOpen
      // The diff pane only makes sense in a workspace, so force it shut when the
      // session is detached — "No diffs" then only ever shows inside a project,
      // never as a second empty panel next to the file browser.
      // Docked (wide): `reviewOpen` gates it. Narrow: drop `reviewOpen` from the
      // gate so the pane stays mounted as a collapsed overlay — `toggleReview`
      // then slides it in/out via the forced-reveal pin, exactly like ⌘B for the
      // sidebar. Still requires a repo (no diffs to show otherwise).
      disabled={!isL2 || !currentCwd.trim() || (!narrowViewport && !reviewOpen)}
      forceCollapsed={narrowViewport}
      hoverReveal
      id={REVIEW_PANE_ID}
      key="review"
      maxWidth={FILE_BROWSER_MAX_WIDTH}
      minWidth={FILE_BROWSER_MIN_WIDTH}
      // Mobile overlay sits at its min width — compact, doesn't bury the chat.
      overlayWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      <ReviewPane key={currentCwd || 'no-cwd'} />
    </Pane>
  )

  // WP-D: the L1 secretary rail, rebuilt on the base Pane system. Same agenda
  // store and same four states the old console `RightRail` rendered — only the
  // chrome moved from a hand-rolled aside to the shell's right rail. Disabled
  // (fully collapsed) everywhere but L1, so the schedule never bleeds into a
  // workspace view.
  const agendaTimelinePane = (
    <Pane
      defaultOpen
      disabled={level !== 'l1'}
      divider
      forceCollapsed={narrowViewport}
      hoverReveal
      id="agenda-timeline"
      key="agenda-timeline"
      maxWidth={FILE_BROWSER_MAX_WIDTH}
      minWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      <aside
        aria-label={t.console.timeline.title}
        className={cn(
          'relative flex h-full w-full min-w-0 flex-col overflow-hidden border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) pt-(--titlebar-height) text-(--ui-text-tertiary)',
          panesFlipped
            ? 'border-r shadow-[inset_-0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
            : 'border-l shadow-[inset_0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
        )}
      >
        <RightSidebarSectionHeader>
          <div className="flex min-w-0 flex-1">
            <SidebarPanelLabel>{t.console.timeline.title}</SidebarPanelLabel>
          </div>
          <Button
            aria-label={t.console.timeline.expand}
            onClick={() => navigate(AGENDA_ROUTE)}
            size="icon-xs"
            title={t.console.timeline.expand}
            variant="ghost"
          >
            <Codicon name="expand-all" size="0.8125rem" />
          </Button>
          <Button
            aria-label={t.console.timeline.addEvent}
            onClick={() => setAgendaAdding(open => !open)}
            size="icon-xs"
            title={t.console.timeline.addEvent}
            variant="ghost"
          >
            <Codicon name="add" size="0.8125rem" />
          </Button>
        </RightSidebarSectionHeader>

        {agendaAdding ? <AddEventForm onClose={() => setAgendaAdding(false)} /> : null}

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
          <Timeline anchors={agendaAnchors} avoidWindows={agendaAvoidWindows} events={agendaEvents} plans={agendaPlans} />
          {agendaRhythmOff && !agendaLoading ? (
            <p className="px-2 pt-1 text-[0.7rem] text-muted-foreground/70" data-testid="timeline-rhythm-off">
              {t.agenda.rhythmOff}
            </p>
          ) : null}
        </div>
      </aside>
    </Pane>
  )

  const terminalPane = (
    <Pane
      bottomRow={terminalAsRow}
      defaultOpen
      disabled={!terminalSidebarOpen}
      divider
      height="38vh"
      id="terminal-sidebar"
      key="terminal-sidebar"
      maxHeight="80vh"
      maxWidth="80vw"
      minHeight="8rem"
      minWidth="22vw"
      resizable
      side={railSide}
      width="42vw"
    >
      {/* As a column the terminal clears the titlebar; as a bottom row it sits
          below the rail's panes (so it fills its row edge-to-edge) and gets a
          left border separating it from the chat — the column-mode separator
          lives on the resize sash, which moves to the top edge as a row. */}
      <div
        className={cn(
          'relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-(--ui-editor-surface-background)',
          terminalAsRow ? 'border-l border-(--ui-stroke-secondary) pt-0' : 'pt-(--titlebar-height)'
        )}
      >
        <TerminalPaneChrome />
      </div>
    </Pane>
  )

  return (
    <AppShell
      leftStatusbarItems={leftStatusbarItems}
      leftTitlebarTools={titlebarToolGroups.flat.left}
      mainOverlays={mainOverlays}
      onOpenSettings={openSettings}
      overlays={overlays}
      previewPaneOpen={secretaryShellOpen && previewRailContent}
      rightRail={
        level === 'l1'
          ? { open: agendaPaneOpen, toggle: () => togglePane('agenda-timeline') }
          : undefined
      }
      statusbarItems={statusbarItems}
      terminalPaneOpen={terminalSidebarOpen}
      titlebarTools={titlebarToolGroups.flat.right}
    >
      {!isSecondaryWindow() && (
        <Pane
          // WP-A: the base sidebar is the shell's left rail for L1/L2 and the
          // legacy chat alike. Overlay modals cover the shell; skills/messaging/
          // artifacts keep this rail (Plobi original) so the page is escapable.
          disabled={!sidebarPaneOpen}
          forceCollapsed={narrowViewport}
          hoverReveal
          id="chat-sidebar"
          maxWidth={SIDEBAR_MAX_WIDTH}
          minWidth={SIDEBAR_DEFAULT_WIDTH}
          onOverlayActiveChange={setSidebarOverlayMounted}
          resizable
          side={sidebarSide}
          width={`${SIDEBAR_DEFAULT_WIDTH}px`}
        >
          {sidebar}
        </Pane>
      )}
      <PaneMain>
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
          {/* Overlay routes: center stays empty; the card mounts via
              `overlays` from `useOverlayRouting`. Missing a stub here lets
              `*` match, null `chatScope` → Navigate home (open full board bounce). */}
          <Route element={null} path="agenda" />
          <Route element={null} path="cron" />
          <Route element={null} path="profiles" />
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
                submit={stableSubmit}
              />
            }
            path="*"
          />
        </Routes>
      </PaneMain>
      {/*
        Order within a side maps to column order. Default (rail on the right):
        main | terminal | preview | review | agenda. Flipped (rail on the left):
        mirror so terminal stays adjacent to the chat.
      */}
      {panesFlipped ? fileBrowserPane : terminalPane}
      {previewPane}
      {reviewPane}
      {agendaTimelinePane}
      {panesFlipped ? terminalPane : fileBrowserPane}
    </AppShell>
  )
}

function SecretaryCenterPane({
  cancel,
  chatScope,
  composer,
  modelMenuContent,
  submit
}: {
  cancel?: ChatSurfaceProps['cancel']
  chatScope: ChatScope | null
  composer?: ChatSurfaceProps['composer']
  modelMenuContent?: ChatSurfaceProps['modelMenuContent']
  submit?: ChatSurfaceProps['submit']
}) {
  if (!chatScope) {
    return <Navigate replace to={HOME_ROUTE} />
  }

  return (
    <div className="flex h-full min-h-0 flex-col pt-[var(--titlebar-height)]">
      <ChatSurface
        cancel={cancel}
        composer={composer}
        modelMenuContent={modelMenuContent}
        scope={chatScope}
        submit={submit}
      />
    </div>
  )
}

// WP-E: `/sessions/:sessionId` (legacy deep link) — the session route itself is
// gone, so land on the L1 shell instead of a dead session hop.
function LegacySessionRedirect() {
  return <Navigate replace to={HOME_ROUTE} />
}
