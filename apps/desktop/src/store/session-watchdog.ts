import { atom } from 'nanostores'

import { toggleMembership, updateAtom, type Updater } from '@/lib/store-utils'
import { $workingSessionIds, setWorkingSessionIds } from './session-ids'

// Watchdog tracking — when does a "working" session count as stuck?
// Long-running tool calls (LLM inference, long shell commands, web fetches)
// can take a few minutes legitimately. We allow 8 minutes of complete
// silence on the stream before clearing the working flag; in practice this
// catches gateway hangs and dropped streams without false-positive-clearing
// real long turns.
const SESSION_WATCHDOG_TIMEOUT_MS = 8 * 60 * 1000
const sessionWatchdogTimers = new Map<string, ReturnType<typeof setTimeout>>()

// Notified (with the stored session id) whenever the watchdog force-clears a
// stuck session. The session-state cache subscribes to also drop that session's
// busy/awaiting flags — clearing `$workingSessionIds` alone only removes the
// sidebar dot, leaving the composer stuck on "Thinking"/Stop for a hung or
// looping turn that never streamed its terminal event.
type SessionWatchdogListener = (storedSessionId: string) => void
const sessionWatchdogListeners = new Set<SessionWatchdogListener>()

export function onSessionWatchdogClear(listener: SessionWatchdogListener): () => void {
  sessionWatchdogListeners.add(listener)

  return () => void sessionWatchdogListeners.delete(listener)
}

function armSessionWatchdog(sessionId: string) {
  const existing = sessionWatchdogTimers.get(sessionId)

  if (existing) {
    clearTimeout(existing)
  }

  const timer = setTimeout(() => {
    sessionWatchdogTimers.delete(sessionId)

    // Re-check the latest state at fire-time. If the user already navigated
    // away or the session genuinely finished, the timer is a no-op.
    if ($workingSessionIds.get().includes(sessionId)) {
      setWorkingSessionIds(current => current.filter(id => id !== sessionId))
    }

    for (const listener of sessionWatchdogListeners) {
      listener(sessionId)
    }
  }, SESSION_WATCHDOG_TIMEOUT_MS)

  sessionWatchdogTimers.set(sessionId, timer)
}

function clearSessionWatchdog(sessionId: string) {
  const existing = sessionWatchdogTimers.get(sessionId)

  if (existing) {
    clearTimeout(existing)
    sessionWatchdogTimers.delete(sessionId)
  }
}

// A session's "working" flag clears the instant its turn ends, but the
// cross-profile aggregator (listSessions with min_messages=1) only sees the
// just-persisted first turn a beat later. The active chat is shielded from that
// race by sessionsToKeep(), but a brand-new session that finished *while you
// were viewing a different chat* is, at the next refresh, neither working,
// pinned, nor active — so mergeSessionPage() evicts it. Nothing re-fetches
// afterward, so it stays gone until the app restarts. (Repro: start a new chat,
// then click another session before the first reply lands.)
//
// To bridge that window we keep a session in the merge keep-set for a short
// grace period after its turn settles, giving the aggregator time to catch up.
// Entries auto-expire, so this never accumulates and can't resurrect a deleted
// session (mergeSessionPage only revives rows still present in the in-memory
// list, which optimistic delete/archive already drops).
const SESSION_SETTLE_GRACE_MS = 30 * 1000
const settledSessionExpiry = new Map<string, number>()

function markSessionSettled(sessionId: string) {
  settledSessionExpiry.set(sessionId, Date.now() + SESSION_SETTLE_GRACE_MS)
}

function clearSessionSettled(sessionId: string) {
  settledSessionExpiry.delete(sessionId)
}

/** Stored ids of sessions whose turn ended within the grace window. Prunes
 *  expired entries as it reads, so it stays bounded without a timer. */
export function getRecentlySettledSessionIds(now: number = Date.now()): string[] {
  const live: string[] = []

  for (const [id, expiry] of settledSessionExpiry) {
    if (expiry > now) {
      live.push(id)
    } else {
      settledSessionExpiry.delete(id)
    }
  }

  return live
}

/** Call when a streaming event for a session lands. Refreshes the watchdog
 *  so the session keeps its "working" status as long as data keeps coming. */
export function noteSessionActivity(sessionId: string | null | undefined) {
  if (!sessionId || !$workingSessionIds.get().includes(sessionId)) {
    return
  }

  armSessionWatchdog(sessionId)
}

// Stored session ids with a blocking prompt (clarify) waiting on the user.
// Separate from $workingSessionIds: a session can be "working" (turn running)
// AND need input. The sidebar row reads this for a persistent indicator that,
// unlike a toast, survives window blur / alt-tab.
export const $attentionSessionIds = atom<string[]>([])
export const setAttentionSessionIds = (next: Updater<string[]>) => updateAtom($attentionSessionIds, next)

export function setSessionAttention(sessionId: string | null | undefined, needsInput: boolean) {
  if (sessionId) {
    toggleMembership(setAttentionSessionIds, sessionId, needsInput)
  }
}

export function setSessionWorking(sessionId: string | null | undefined, working: boolean) {
  if (!sessionId) {
    return
  }

  const wasWorking = $workingSessionIds.get().includes(sessionId)

  toggleMembership(setWorkingSessionIds, sessionId, working)

  // Bookend the watchdog: arm on enter, disarm on leave. A later
  // noteSessionActivity() from a streaming event refreshes the timer.
  if (working) {
    clearSessionSettled(sessionId)
    armSessionWatchdog(sessionId)
  } else {
    clearSessionWatchdog(sessionId)

    // Only grant grace on a real working→idle transition (updateSessionState
    // re-asserts `false` on every state tick, which must not keep extending the
    // window). This keeps the just-finished session visible long enough for the
    // aggregator to return its now-persisted row.
    if (wasWorking) {
      markSessionSettled(sessionId)
    }
  }
}
