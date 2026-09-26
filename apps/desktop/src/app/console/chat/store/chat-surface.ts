/**
 * Console agenda-propose helper (spec §3.6 shared pending-event card).
 *
 * The console's L1/L2 conversation no longer lives in a console-local store:
 * per ARCH-RULINGS 2026-09-02 the center reuses the base session store + gateway
 * path (the same `$messages` / `submitText` the full-screen chat uses), so there
 * is no scope-keyed message map, no `sendChat`, and no mock transport here.
 *
 * This module keeps only the `agenda_propose` tool-part builder shared by the
 * agenda right rail and any future console composer that wants to inject a
 * confirmation card into the (base) message stream.
 */

import type { ChatMessagePart } from '@/lib/chat-messages'
import type { AgendaEvent } from '@/types/plobi'

/** Tool name the assistant uses to propose a pending agenda event (U2). */
export const AGENDA_PROPOSE_TOOL = 'agenda_propose'

/**
 * Build the pending-event card message part (spec §3.6). The card is a SHARED
 * message-card type dispatched by the base `message-parts.tsx` from the tool
 * call part — the single `ChatSurface` renders it identically for L1 and L2.
 */
export function proposePart(event: AgendaEvent): ChatMessagePart {
  // `AgendaEvent` is a concrete interface, not a JSON index-signature record,
  // so it does not structurally satisfy the `args` type the tool-call part
  // declares — hence the double assertion. The payload is plain JSON at
  // runtime.
  return {
    args: event,
    toolCallId: `propose-${event.id}`,
    toolName: AGENDA_PROPOSE_TOOL,
    type: 'tool-call'
  } as unknown as ChatMessagePart
}
