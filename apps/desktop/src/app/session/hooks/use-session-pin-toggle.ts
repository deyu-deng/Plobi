import { useCallback } from 'react'

import { $pinnedSessionIds, pinSession, unpinSession } from '@/store/layout'
import { $selectedStoredSessionId, $sessions, sessionPinId } from '@/store/session'

/**
 * WP-DESKTOP-HOOKS-1: toggle the currently selected stored session's pin.
 * Pin on the durable lineage-root id so the pin survives auto-compression.
 */
export function useSessionPinToggle() {
  const toggleSelectedPin = useCallback(() => {
    const sessionId = $selectedStoredSessionId.get()

    if (!sessionId) {
      return
    }

    const session = $sessions.get().find(s => s.id === sessionId || s._lineage_root_id === sessionId)
    const pinId = session ? sessionPinId(session) : sessionId

    if ($pinnedSessionIds.get().includes(pinId)) {
      unpinSession(pinId)
    } else {
      pinSession(pinId)
    }
  }, [])

  return { toggleSelectedPin }
}
