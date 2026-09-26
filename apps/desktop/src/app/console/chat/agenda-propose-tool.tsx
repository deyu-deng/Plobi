/**
 * `agenda_propose` tool renderer — mounts the pending-event card inside the
 * base chat pipeline (U2, spec §3.2).
 *
 * [SPEC-QUESTION] the spec freezes the card's content (old/new comparison +
 * sequence number + confirm/ignore) but not the tool name that carries it.
 * `agenda_propose` follows the same dispatch pattern as `clarify` /
 * `image_generate` in `ChainToolFallback`.
 *
 * The event payload is the SAME `AgendaEvent` row the board and right rail
 * render (spec §3.2: "与钉钉卡同一数据模型"), so all confirmation surfaces
 * agree. Confirm/ignore goes through the console API layer only.
 */

import { useState } from 'react'

import { useI18n } from '@/i18n'
import type { AgendaEvent } from '@/types/plobi'

import { confirmAgendaEventAction } from '../api'

import { PendingEventCard } from './pending-event-card'

function parseEvent(args: unknown): AgendaEvent | null {
  if (typeof args !== 'object' || args === null) {
    return null
  }

  const candidate = args as Partial<AgendaEvent>

  return typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.start_at === 'string'
    ? (candidate as AgendaEvent)
    : null
}

/**
 * Narrowed on purpose: only `args` is read, so tests and future callers can
 * mount the card with a bare event payload. `ChainToolFallback` spreads the
 * full `ToolCallMessagePartProps` in — the extra keys are simply ignored.
 */
export function AgendaProposeTool({ args }: { args?: unknown }) {
  const { t } = useI18n()
  const c = t.console.chat
  const event = parseEvent(args)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<'confirmed' | 'dismissed' | 'error' | null>(null)

  if (!event) {
    return <div className="my-2 text-[0.75rem] text-(--ui-text-tertiary)">{c.cardInvalid}</div>
  }

  if (outcome) {
    return (
      <div className="my-2 border-l-2 border-(--ui-border) pl-3 text-[0.75rem] text-(--ui-text-tertiary)">
        {outcome === 'confirmed' ? c.cardConfirmed : outcome === 'dismissed' ? c.cardDismissed : c.cardFailed}
      </div>
    )
  }

  const act = async (action: 'confirm' | 'dismiss') => {
    setBusy(true)

    try {
      await confirmAgendaEventAction(event.id, action)
      setOutcome(action === 'confirm' ? 'confirmed' : 'dismissed')
    } catch {
      setOutcome('error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <PendingEventCard
      busy={busy}
      event={event}
      onConfirm={() => void act('confirm')}
      onDismiss={() => void act('dismiss')}
    />
  )
}
