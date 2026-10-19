import { useCallback, useEffect, useRef, type MutableRefObject } from 'react'
import { useStore } from '@nanostores/react'

import { $currentCwd, setActiveSessionId, setAwaitingResponse, setBusy, setCurrentBranch, setCurrentCwd, setFreshDraftReady, setMessages, setSelectedStoredSessionId } from '@/store/session'
import { normalizeProfileKey, $activeGatewayProfile, $freshSessionRequest } from '@/store/profile'
import { resolveNewSessionCwd } from '@/store/projects'
import { forgetL1MainSession, restartsMouthInPlace } from '../../desktop-controller-utils'
import type { ShellLevel } from '../../routes'

interface ConversationResetOptions {
  busyRef: MutableRefObject<boolean>
  activeSessionIdRef: MutableRefObject<string | null>
  selectedStoredSessionIdRef: MutableRefObject<string | null>
  level: null | ShellLevel
  startFreshSessionDraft: () => void
}

/**
 * WP-DESKTOP-HOOKS-5: reset the conversation surface in place — the "nothing
 * to resume yet" empty draft shared by level-keyed binds and 「重新开始」, plus
 * the single listener that drops to a fresh draft after a profile
 * switch/create.
 */
export function useConversationReset({
  busyRef,
  activeSessionIdRef,
  selectedStoredSessionIdRef,
  level,
  startFreshSessionDraft
}: ConversationResetOptions) {
  // The "nothing to resume yet" state of a secretary shell: an empty draft under
  // the current route. Shared by the level-keyed bind below and 「重新開始」 so a
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

  return { startFreshConversation, prepareFreshDraftInPlace }
}
