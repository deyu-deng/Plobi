import { type MutableRefObject, useCallback, useRef } from 'react'

import { preserveLocalAssistantErrors, toChatMessages } from '@/lib/chat-messages'
import { isMessagingSource } from '@/lib/session-source'
import { latestSessionTodos } from '@/lib/todos'
import { getSessionMessages, type SessionMessage } from '@/plobi'
import { $messagingSessions, $sessions } from '@/store/session'
import { clearSessionTodos, setSessionTodos, todosForHydration } from '@/store/todos'
import type { ClientSessionState } from '../../types'

interface TranscriptRehydrationOptions {
  activeSessionIdRef: MutableRefObject<string | null>
  busyRef: MutableRefObject<boolean>
  selectedStoredSessionIdRef: MutableRefObject<string | null>
  updateSessionState: (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState,
    storedSessionId?: string
  ) => ClientSessionState
}

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

/**
 * WP-DESKTOP-HOOKS-4: rehydrate a stored session's transcript into its runtime
 * session after a turn completes, and poll-refresh the active messaging
 * transcript when its backend content changes.
 */
export function useTranscriptRehydration({
  activeSessionIdRef,
  busyRef,
  selectedStoredSessionIdRef,
  updateSessionState
}: TranscriptRehydrationOptions) {
  const messagingTranscriptSignatureRef = useRef(new Map<string, string>())

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

  return { hydrateFromStoredSession, refreshActiveMessagingTranscript }
}
