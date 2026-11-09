import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import type { NavigateFunction } from 'react-router-dom'

import { isFocusWithin } from '@/lib/keybinds/combo'

import { storedSessionIdForNotification } from '../../lib/session-ids'
import { respondToApprovalAction } from '../../store/native-notifications'
import { getRememberedSessionId, setRememberedSessionId } from '../../store/session'
import { openUpdatesWindow, startUpdatePoller, stopUpdatePoller } from '../../store/updates'
import { requestComposerFocus, requestComposerInsert } from '../chat/composer/focus'
import { closeActiveTerminal } from '../right-sidebar/terminal/terminals'
import { NEW_CHAT_ROUTE, sessionRoute } from '../routes'

export interface UseDesktopBridgeOptions {
  secretaryShellOpen: boolean
  /** 对话旁那一面上现在有没有可看的内容（旧的预览轨删了 ⇒ 这里指工作区卡片）。 */
  previewSurfaceContent: unknown
  routedSessionId: string | null | undefined
  locationPathname: string
  navigate: NavigateFunction
  resumeExhaustedSessionId: string | null | undefined
  runtimeIdByStoredSessionIdRef: RefObject<Map<string, string>>
}

export function useDesktopBridge(options: UseDesktopBridgeOptions) {
  const {
    secretaryShellOpen,
    previewSurfaceContent,
    routedSessionId,
    locationPathname,
    navigate,
    resumeExhaustedSessionId,
    runtimeIdByStoredSessionIdRef
  } = options

  useEffect(() => {
    window.plobiDesktop?.setPreviewShortcutActive?.(
      Boolean(secretaryShellOpen && previewSurfaceContent)
    )
  }, [previewSurfaceContent, secretaryShellOpen])

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

    if (last && locationPathname === NEW_CHAT_ROUTE) {
      navigate(sessionRoute(last), { replace: true })
    }
  }, [locationPathname, navigate])

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
      }
    }

    window.addEventListener('keydown', onKeyDown, { capture: true })

    return () => {
      window.removeEventListener('keydown', onKeyDown, { capture: true })
    }
  }, [])
}
