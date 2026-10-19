import { useEffect } from 'react'
import { useStore } from '@nanostores/react'
import { isMessagingSource } from '../../lib/session-source'
import { $messagingSessions, $selectedStoredSessionId } from '../../store/session'

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

export interface UseBackgroundPollingOptions {
  gatewayState: string
  refreshCronJobs: () => void
  refreshMessagingSessions: () => void
  refreshActiveMessagingTranscript: () => void
}

export function useBackgroundPolling(options: UseBackgroundPollingOptions) {
  const { gatewayState, refreshCronJobs, refreshMessagingSessions, refreshActiveMessagingTranscript } = options
  const messagingSessions = useStore($messagingSessions)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)

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
}
