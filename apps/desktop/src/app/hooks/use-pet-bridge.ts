import { useEffect } from 'react'
import { setPetActivity } from '../../store/pet'
import { type GatewayRequest, setPetScale } from '../../store/pet-gallery'
import {
  setPetOverlayOpenAppHandler,
  setPetOverlayScaleHandler,
  setPetOverlaySubmitHandler
} from '../../store/pet-overlay'
import { $attentionSessionIds, $sessions } from '../../store/session'
import { isSecondaryWindow } from '../../store/windows'

export interface UsePetBridgeOptions {
  submitTextRef: React.MutableRefObject<(text: string) => void>
  requestGatewayRef: React.MutableRefObject<GatewayRequest>
  resumeSessionRef: React.MutableRefObject<(id: string) => void>
}

export function usePetBridge(options: UsePetBridgeOptions) {
  const { submitTextRef, requestGatewayRef, resumeSessionRef } = options

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
}
