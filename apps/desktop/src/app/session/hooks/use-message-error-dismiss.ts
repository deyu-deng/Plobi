import { type MutableRefObject, useCallback } from 'react'

import { chatMessageText, type ChatMessage } from '@/lib/chat-messages'
import { $messages, setMessages } from '@/store/session'
import type { ClientSessionState } from '../../types'

interface MessageErrorDismissOptions {
  activeSessionIdRef: MutableRefObject<string | null>
  updateSessionState: (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState,
    storedSessionId?: string
  ) => ClientSessionState
}

/**
 * WP-DESKTOP-HOOKS-2: clear a failed turn's red error banner from the
 * transcript. Errors are renderer-local state (never persisted), so dismissing
 * is purely a view + session-cache edit. A message that errored before emitting
 * any visible text is a bare error placeholder → drop it entirely; one that
 * streamed partial output then failed keeps its content and just sheds the
 * error. Both the per-runtime cache AND the live $messages view must be
 * updated: `preserveLocalAssistantErrors` re-grafts any still-errored message
 * it finds in the view onto the next session.info flush, so clearing only the
 * cache would let the heartbeat resurrect the banner.
 */
export function useMessageErrorDismiss({ activeSessionIdRef, updateSessionState }: MessageErrorDismissOptions) {
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

  return { dismissError }
}
