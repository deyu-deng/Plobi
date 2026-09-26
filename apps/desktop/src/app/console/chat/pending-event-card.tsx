import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import type { AgendaEvent } from '@/types/plobi'

/**
 * Pending event card — the chat-embedded form of a schedule change
 * (spec §3.2, sharing one data model with the DingTalk card in §7).
 *
 * Reuse: the same `AgendaEvent` row the board and the right rail render, so the
 * three confirmation surfaces (chat card / rail button / DingTalk reply) can
 * never disagree. Only `Button` and tokens are used — no card-in-card, per
 * DESIGN.md.
 *
 * Mounting: rendered from the assistant-ui tool-call part pipeline
 * (`ChainToolFallback` in `components/assistant-ui/thread/message-parts.tsx`),
 * which is how `clarify` and `image_generate` attach their custom UI.
 */

const MAX_SNIPPET = 50

function clockOf(iso: string): string {
  return iso.length >= 16 ? iso.slice(11, 16) : iso
}

function dayOf(iso: string): string {
  return iso.length >= 10 ? iso.slice(0, 10) : iso
}

function whenOf(iso: string): string {
  return `${dayOf(iso)} ${clockOf(iso)}`
}

function truncate(text: string): string {
  const cleaned = text.trim()

  return cleaned.length > MAX_SNIPPET ? `${cleaned.slice(0, MAX_SNIPPET)}…` : cleaned
}

interface PendingEventCardProps {
  busy?: boolean
  event: AgendaEvent
  onConfirm: (eventId: string) => void
  onDismiss: (eventId: string) => void
}

export function PendingEventCard({ busy = false, event, onConfirm, onDismiss }: PendingEventCardProps) {
  const { t } = useI18n()
  const c = t.console

  const previous = event.prev_value

  return (
    <div className="my-2 flex flex-col gap-2 border-l-2 border-(--ui-accent) pl-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-[0.7rem] font-medium tabular-nums text-(--ui-text-tertiary)">
          {c.pendingCard.number(event.confirm_seq ?? 0)}
        </span>
        <span className="text-[0.82rem] font-medium text-foreground/90">{event.title}</span>
      </div>

      {previous?.start_at ? (
        <div className="flex flex-wrap items-baseline gap-1.5 text-[0.75rem]">
          <span className="text-(--ui-text-tertiary)">{c.pendingCard.oldValue}</span>
          <span className="tabular-nums text-(--ui-text-secondary) line-through">
            {whenOf(previous.start_at)}
          </span>
          <span aria-hidden="true" className="text-(--ui-text-tertiary)">
            →
          </span>
          <span className="font-medium tabular-nums text-foreground/85">
            {whenOf(event.start_at)}
          </span>
        </div>
      ) : (
        <div className="text-[0.75rem] tabular-nums text-(--ui-text-secondary)">
          {whenOf(event.start_at)}
        </div>
      )}

      {event.evidence?.snippet ? (
        <p className="text-[0.7rem] text-(--ui-text-tertiary)">
          {c.pendingCard.source(event.source, truncate(String(event.evidence.snippet)))}
        </p>
      ) : null}

      <div className="flex items-center gap-1.5">
        <Button disabled={busy} onClick={() => onConfirm(event.id)} size="xs">
          {c.pendingCard.confirm}
        </Button>
        <Button disabled={busy} onClick={() => onDismiss(event.id)} size="xs" variant="secondary">
          {c.pendingCard.dismiss}
        </Button>
      </div>
    </div>
  )
}
