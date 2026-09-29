import { useStore } from '@nanostores/react'
import { useCallback, useMemo } from 'react'

import type { CommandCenterSection } from '@/app/command-center'
import { $terminalTakeover, setTerminalTakeover } from '@/app/right-sidebar/store'
import { ContextUsagePanel } from '@/app/shell/context-usage-panel'
import { GatewayMenuPanel } from '@/app/shell/gateway-menu-panel'
import { Codicon } from '@/components/ui/codicon'
import { GlyphSpinner } from '@/components/ui/glyph-spinner'
import { useI18n } from '@/i18n'
import { deferredRowLabel, readDeferredLedgerView } from '@/lib/desktop-deferred-sidecars'
import { Activity, AlertCircle, Clock, Command, Hash, Loader2, Terminal, Zap, ZapFilled } from '@/lib/icons'
import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { contextBarLabel, LiveDuration, usageContextLabel } from '@/lib/statusbar'
import { cn } from '@/lib/utils'
import { setGlobalYolo, setSessionYolo } from '@/lib/yolo-session'
import { $desktopBoot } from '@/store/boot'
import {
  $activeSessionId,
  $busy,
  $connection,
  $currentUsage,
  $sessionStartedAt,
  $turnStartedAt,
  $yoloActive,
  setYoloActive
} from '@/store/session'
import { $subagentsBySession, activeSubagentCount, failedSubagentCount } from '@/store/subagents'
import { $gatewayRestarting } from '@/store/system-actions'
import {
  $backendUpdateApply,
  $backendUpdateStatus,
  $desktopVersion,
  $updateApply,
  $updateStatus,
  openUpdateOverlayFor
} from '@/store/updates'
import type { StatusResponse } from '@/types/plobi'

import { $agendaPendingCount } from '@/store/agenda'

import { AGENDA_ROUTE, CRON_ROUTE } from '../../routes'
import type { StatusbarItem, StatusbarSelectModifiers } from '../statusbar-controls'

interface StatusbarItemsOptions {
  agentsOpen: boolean
  chatOpen: boolean
  commandCenterOpen: boolean
  extraLeftItems: readonly StatusbarItem[]
  extraRightItems: readonly StatusbarItem[]
  gatewayState: string
  inferenceStatus: RuntimeReadinessResult | null
  openAgents: () => void
  openCommandCenterSection: (section: CommandCenterSection) => void
  freshDraftReady: boolean
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  /** True on L1/L2 secretary shell and legacy chat — shows the terminal toggle. */
  shellOpen?: boolean
  statusSnapshot: StatusResponse | null
  toggleCommandCenter: () => void
}

export function useStatusbarItems({
  agentsOpen,
  chatOpen,
  commandCenterOpen,
  extraLeftItems,
  extraRightItems,
  gatewayState,
  inferenceStatus,
  openAgents,
  openCommandCenterSection,
  freshDraftReady,
  requestGateway,
  shellOpen = false,
  statusSnapshot,
  toggleCommandCenter
}: StatusbarItemsOptions) {
  const { t } = useI18n()
  const copy = t.shell.statusbar
  const activeSessionId = useStore($activeSessionId)
  const terminalTakeover = useStore($terminalTakeover)
  const yoloActive = useStore($yoloActive)
  const busy = useStore($busy)
  const currentUsage = useStore($currentUsage)
  const gatewayRestarting = useStore($gatewayRestarting)
  const sessionStartedAt = useStore($sessionStartedAt)
  const turnStartedAt = useStore($turnStartedAt)
  const subagentsBySession = useStore($subagentsBySession)
  const agendaPendingCount = useStore($agendaPendingCount)
  const updateStatus = useStore($updateStatus)
  const updateApply = useStore($updateApply)
  const backendUpdateStatus = useStore($backendUpdateStatus)
  const backendUpdateApply = useStore($backendUpdateApply)
  const desktopVersion = useStore($desktopVersion)
  const connection = useStore($connection)
  // R-048/R-052 — the sidecar ledger rides this SAME gateway dot instead of
  // raising a card: `deferred` means "nothing here could even be started", which
  // is a priority decision, not a fault. `red` rows are listed beside them so the
  // dot menu carries the reason and the enable steps for both.
  const bootSidecars = useStore($desktopBoot).sidecars
  const sidecarView = useMemo(() => readDeferredLedgerView(bootSidecars), [bootSidecars])

  const contextUsage = useMemo(() => usageContextLabel(currentUsage), [currentUsage])
  const contextBar = useMemo(() => contextBarLabel(currentUsage), [currentUsage])

  // Per-session approval bypass (same scope as the TUI's Shift+Tab). On a
  // new-chat draft (no runtime session yet) we arm locally; the session-create
  // path applies it once the backend session exists.
  //
  // Shift+click flips the GLOBAL approvals.mode instead — a persistent,
  // all-sessions/CLI/TUI/cron bypass that survives restarts.
  const toggleYolo = useCallback(
    async (modifiers?: StatusbarSelectModifiers) => {
      const next = !$yoloActive.get()

      setYoloActive(next)

      if (modifiers?.shiftKey) {
        try {
          await setGlobalYolo(requestGateway, next)
        } catch {
          setYoloActive(!next)
        }

        return
      }

      const sid = $activeSessionId.get()

      if (!sid) {
        return
      }

      try {
        await setSessionYolo(requestGateway, sid, next)
      } catch {
        setYoloActive(!next)
      }
    },
    [requestGateway]
  )

  const showYoloToggle = gatewayState === 'open' && (!!activeSessionId || freshDraftReady)

  const gatewayMenuContent = useMemo(
    () => (close: () => void) => (
      <>
        <GatewayMenuPanel
          gatewayState={gatewayState}
          inferenceStatus={inferenceStatus}
          onClose={close}
          onOpenSystem={() => openCommandCenterSection('system')}
          statusSnapshot={statusSnapshot}
        />
        {sidecarView.noted.length > 0 && (
          <div className="border-t border-border/50 px-3 py-2">
            <div className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
              {t.boot.sidecars.deferredTitle}
            </div>
            <p className="mt-1 text-[0.66rem] leading-snug text-muted-foreground">{t.boot.sidecars.deferredHint}</p>
            <ul className="mt-1.5 space-y-1.5">
              {sidecarView.noted.map(row => (
                <li className="flex flex-col gap-0.5 text-xs" key={row.id}>
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate">{`${deferredRowLabel(row)} (:${row.port})`}</span>
                    <span
                      className={cn(
                        'shrink-0 text-[0.66rem]',
                        row.state === 'red' ? 'text-destructive' : 'text-muted-foreground'
                      )}
                    >
                      {row.state}
                    </span>
                  </span>
                  <span className="leading-snug text-muted-foreground">{row.observed}</span>
                  <span className="leading-snug text-muted-foreground/80">
                    {`${t.boot.sidecars.enableLabel}: ${row.enableHint}`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </>
    ),
    [gatewayState, inferenceStatus, openCommandCenterSection, sidecarView, statusSnapshot, t.boot.sidecars]
  )

  // The dot's one-line note: how many sidecars the gateway is carrying for. Null
  // when every row is ready/probing, which is what makes the dot go quiet again.
  const sidecarNote =
    sidecarView.noted.length > 0 ? t.boot.sidecars.statusbarDeferred(sidecarView.noted.length) : null

  // The indicator must speak the same scope as the Spawn-tree panel it opens:
  // every session's subagents, never background system actions (gateway
  // restarts, toolset installs) which surface in their own panels.
  const { subagentsFailed, subagentsRunning } = useMemo(() => {
    const lists = Object.values(subagentsBySession)

    return {
      subagentsFailed: lists.reduce((sum, items) => sum + failedSubagentCount(items), 0),
      subagentsRunning: lists.reduce((sum, items) => sum + activeSubagentCount(items), 0)
    }
  }, [subagentsBySession])

  const gatewayOpen = gatewayState === 'open'
  const gatewayConnecting = gatewayState === 'connecting'
  const inferenceReady = gatewayOpen && inferenceStatus?.ready === true
  const gatewayDegraded = gatewayOpen || gatewayConnecting

  const gatewayDetail = gatewayOpen
    ? inferenceStatus?.ready
      ? copy.gatewayReady
      : inferenceStatus
        ? copy.gatewayNeedsSetup
        : copy.gatewayChecking
    : gatewayConnecting
      ? copy.gatewayConnecting
      : copy.gatewayOffline

  const gatewayClassName = inferenceReady
    ? undefined
    : gatewayDegraded
      ? 'text-amber-600 hover:text-amber-600'
      : 'text-destructive hover:text-destructive'

  const clientVersionItem = useMemo<StatusbarItem>(() => {
    const appVersion = desktopVersion?.appVersion
    const sha = updateStatus?.currentSha?.slice(0, 7) ?? null
    const behind = updateStatus?.behind ?? 0
    const applying = updateApply.applying || updateApply.stage === 'restart'
    const remote = connection?.mode === 'remote'

    const version = appVersion ? `v${appVersion}` : (sha ?? copy.unknown)
    const base = remote ? copy.clientLabel(appVersion ?? sha ?? copy.unknown) : version
    const behindHint = !applying && behind > 0 ? ` (+${behind})` : ''

    const label = applying
      ? `${base} · ${updateApply.stage === 'restart' ? copy.restart : copy.update}`
      : `${base}${behindHint}`

    const tooltip = [
      applying ? updateApply.message || copy.updateInProgress : null,
      !applying && behind > 0 && copy.commitsBehind(behind, updateStatus?.branch ?? '...'),
      appVersion && copy.desktopVersion(appVersion),
      sha && copy.commit(sha),
      updateStatus?.branch && copy.branch(updateStatus.branch)
    ]
      .filter(Boolean)
      .join(' · ')

    return {
      className: !applying && behind > 0 ? 'text-primary hover:text-primary' : undefined,
      detail: appVersion && sha && !applying && !remote ? sha : undefined,
      hidden: !appVersion && !sha,
      icon: applying ? <Loader2 className="size-3 animate-spin" /> : <Hash className="size-3" />,
      id: 'version-client',
      label,
      onSelect: () => openUpdateOverlayFor('client'),
      title: tooltip || undefined,
      variant: 'action'
    }
  }, [
    desktopVersion?.appVersion,
    connection?.mode,
    copy,
    updateApply.applying,
    updateApply.message,
    updateApply.stage,
    updateStatus?.behind,
    updateStatus?.branch,
    updateStatus?.currentSha
  ])

  const backendVersionItem = useMemo<StatusbarItem | null>(() => {
    if (connection?.mode !== 'remote') {
      return null
    }

    const backendVersion = statusSnapshot?.version
    const behind = backendUpdateStatus?.behind ?? 0
    const updateAvailable = backendUpdateStatus?.updateAvailable || behind > 0
    const applying = backendUpdateApply.applying || backendUpdateApply.stage === 'restart'

    const base = copy.backendLabel(backendVersion ?? copy.unknown)

    const behindHint =
      !applying && behind > 0 ? ` (+${behind})` : !applying && updateAvailable ? ` (${copy.update})` : ''

    const label = applying
      ? `${base} · ${backendUpdateApply.stage === 'restart' ? copy.restart : copy.update}`
      : `${base}${behindHint}`

    const tooltip = [
      applying ? backendUpdateApply.message || copy.updateInProgress : null,
      !applying && behind > 0 && copy.commitsBehind(behind, 'main'),
      !applying && behind <= 0 && updateAvailable && copy.update,
      backendVersion && copy.backendVersion(backendVersion)
    ]
      .filter(Boolean)
      .join(' · ')

    return {
      className: !applying && updateAvailable ? 'text-primary hover:text-primary' : undefined,
      hidden: !backendVersion,
      icon: applying ? <Loader2 className="size-3 animate-spin" /> : <Hash className="size-3" />,
      id: 'version-backend',
      label,
      onSelect: () => openUpdateOverlayFor('backend'),
      title: tooltip || undefined,
      variant: 'action'
    }
  }, [
    connection?.mode,
    statusSnapshot?.version,
    backendUpdateStatus?.behind,
    backendUpdateStatus?.updateAvailable,
    backendUpdateApply.applying,
    backendUpdateApply.message,
    backendUpdateApply.stage,
    copy
  ])

  const coreLeftStatusbarItems = useMemo<readonly StatusbarItem[]>(
    () => [
      {
        className: `w-7 justify-center px-0${commandCenterOpen ? ' bg-accent/55 text-foreground' : ''}`,
        icon: <Command className="size-3.5" />,
        id: 'command-center',
        onSelect: toggleCommandCenter,
        title: commandCenterOpen ? copy.closeCommandCenter : copy.openCommandCenter,
        variant: 'action'
      },
      {
        className: gatewayRestarting
          ? undefined
          : sidecarView.red.length > 0
            ? 'text-destructive hover:text-destructive'
            : gatewayClassName,
        detail: gatewayRestarting
          ? copy.gatewayRestarting
          : sidecarNote
            ? `${gatewayDetail} · ${sidecarNote}`
            : gatewayDetail,
        icon: gatewayRestarting ? (
          <GlyphSpinner ariaLabel={copy.gatewayRestarting} className="size-3" />
        ) : inferenceReady ? (
          <Activity className="size-3" />
        ) : (
          <AlertCircle className="size-3" />
        ),
        id: 'gateway-health',
        label: copy.gateway,
        menuClassName: 'w-72',
        menuContent: gatewayMenuContent,
        title: inferenceStatus?.reason || copy.gatewayTitle,
        variant: 'menu'
      },
      {
        className: cn(
          agentsOpen && 'bg-accent/55 text-foreground',
          subagentsFailed > 0 && 'text-destructive hover:text-destructive'
        ),
        detail:
          subagentsRunning > 0
            ? copy.subagents(subagentsRunning)
            : subagentsFailed > 0
              ? copy.failed(subagentsFailed)
              : undefined,
        icon:
          subagentsFailed > 0 ? (
            <AlertCircle className="size-3" />
          ) : subagentsRunning > 0 ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Codicon name="hubot" size="0.75rem" />
          ),
        id: 'agents',
        label: copy.agents,
        onSelect: openAgents,
        title: agentsOpen ? copy.closeAgents : copy.openAgents,
        variant: 'action'
      },
      {
        icon: <Clock className="size-3" />,
        id: 'cron',
        label: copy.cron,
        title: copy.openCron,
        to: CRON_ROUTE,
        variant: 'action'
      },
      {
        // Pending entries are the one thing the user must act on, so the
        // count rides in the statusbar rather than hiding inside the board.
        className: cn(agendaPendingCount > 0 && 'text-amber-500 hover:text-amber-500'),
        detail: agendaPendingCount > 0 ? copy.agendaPending(agendaPendingCount) : undefined,
        icon: <Codicon name="calendar" size="0.75rem" />,
        id: 'agenda',
        label: copy.agenda,
        title: copy.openAgenda,
        to: AGENDA_ROUTE,
        variant: 'action'
      }
    ],
    [
      agendaPendingCount,
      agentsOpen,
      commandCenterOpen,
      copy,
      gatewayMenuContent,
      gatewayClassName,
      gatewayDetail,
      gatewayRestarting,
      inferenceReady,
      inferenceStatus?.reason,
      openAgents,
      sidecarNote,
      sidecarView.red.length,
      subagentsFailed,
      subagentsRunning,
      toggleCommandCenter
    ]
  )

  const coreRightStatusbarItems = useMemo<readonly StatusbarItem[]>(
    () => [
      {
        detail: <LiveDuration since={turnStartedAt} />,
        hidden: !busy || !turnStartedAt,
        icon: <Loader2 className="size-3 animate-spin" />,
        id: 'running-timer',
        label: copy.turnRunning,
        title: copy.currentTurnElapsed,
        variant: 'text'
      },
      {
        detail: contextBar || undefined,
        hidden: !contextUsage,
        id: 'context-usage',
        label: contextUsage,
        menuAlign: 'end',
        menuClassName: 'w-auto border-(--ui-stroke-secondary) p-0',
        menuContent: (
          <ContextUsagePanel currentUsage={currentUsage} requestGateway={requestGateway} sessionId={activeSessionId} />
        ),
        title: copy.openContextUsage,
        variant: 'menu'
      },
      {
        detail: <LiveDuration since={sessionStartedAt} />,
        hidden: !sessionStartedAt,
        id: 'session-timer',
        label: copy.session,
        title: copy.runtimeSessionElapsed,
        variant: 'text'
      },
      {
        className: cn('px-1', yoloActive && 'bg-(--chrome-action-hover)'),
        hidden: !showYoloToggle,
        icon: yoloActive ? (
          <ZapFilled className="size-3.5 shrink-0" />
        ) : (
          <Zap className="size-3.5 shrink-0 opacity-70" />
        ),
        id: 'yolo',
        onSelect: modifiers => void toggleYolo(modifiers),
        title: yoloActive ? copy.yoloOn : copy.yoloOff,
        variant: 'action'
      },
      {
        className: `w-7 justify-center px-0${terminalTakeover ? ' bg-accent/55 text-foreground' : ''}`,
        // L1/L2 share the secretary shell — the terminal toggle must stay
        // visible there too (chatOpen alone only covers the legacy /new path).
        hidden: !chatOpen && !shellOpen,
        icon: <Terminal className="size-3.5" />,
        id: 'terminal',
        onSelect: () => setTerminalTakeover(!$terminalTakeover.get()),
        title: terminalTakeover ? copy.hideTerminal : copy.showTerminal,
        variant: 'action'
      },
      clientVersionItem,
      ...(backendVersionItem ? [backendVersionItem] : [])
    ],
    [
      activeSessionId,
      backendVersionItem,
      busy,
      chatOpen,
      clientVersionItem,
      contextBar,
      contextUsage,
      copy,
      currentUsage,
      requestGateway,
      sessionStartedAt,
      shellOpen,
      showYoloToggle,
      terminalTakeover,
      toggleYolo,
      turnStartedAt,
      yoloActive
    ]
  )

  const leftStatusbarItems = useMemo(
    () => [...coreLeftStatusbarItems, ...extraLeftItems],
    [coreLeftStatusbarItems, extraLeftItems]
  )

  const statusbarItems = useMemo(
    () => [...extraRightItems, ...coreRightStatusbarItems],
    [coreRightStatusbarItems, extraRightItems]
  )

  return { leftStatusbarItems, statusbarItems }
}
