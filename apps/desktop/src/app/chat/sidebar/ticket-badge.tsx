import { useState } from 'react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import {
  AlertTriangle, ArchiveOff, CheckCircle2, Clock, iconSize, Loader2, X
} from '@/lib/icons'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

/** 一张派工票的固化授权（`Ticket.as_dict()` 的屏上子集）。 */
export type DispatchTicket = {
  created_at: number
  goal: string
  id: string
  leg: string
  parent_agent: string
  policy_version: string
  refs: Record<string, string>
  revoke_reason: string
  scope: Record<string, unknown>
  status: string
  triggered_by: string
  updated_at: number
}

function statusIcon(status: string): IconComponent {
  switch (status) {
    case 'active': return Loader2

    case 'done': return CheckCircle2

    case 'failed': return AlertTriangle

    case 'revoked': return ArchiveOff

    default: return Clock
  }
}

function decide(id: string, action: 'activate' | 'revoke', reason?: string): Promise<void> {
  return window.plobiDesktop
    .api<{ error?: string, ok: boolean }>({
      body: reason ? { reason } : {},
      method: 'POST',
      path: `/api/tickets/${encodeURIComponent(id)}/${action}`
    })
    .then(body => {
      if (!body?.ok) {
        throw new Error(body?.error || 'request failed')
      }
    })
}

/** L3 行内那一枚：状态图标 + 票号尾码；点开看固化授权，active 票可撤销。 */
export function TicketBadge({ status, ticketId }: { status?: string, ticketId?: string }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<DispatchTicket | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!ticketId) {
    return null
  }

  const Status = statusIcon(status || '')

  const load = () => {
    void window.plobiDesktop
      .api<{ data?: { ticket?: DispatchTicket }, error?: string, ok: boolean }>({
        path: `/api/tickets/${encodeURIComponent(ticketId)}`
      })
      .then(body => {
        if (!body?.ok) {
          throw new Error(body?.error || 'load failed')
        }

        setDetail(body.data?.ticket ?? null)
        setError('')
      })
      .catch((reason: unknown) => setError(String(reason)))
  }

  const revoke = () => {
    setBusy(true)
    void decide(ticketId, 'revoke', 'revoked from desktop')
      .then(() => {
        setDetail(null)
        setOpen(false)
      })
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setBusy(false))
  }

  const allowed = Array.isArray(detail?.scope?.allowed_capabilities)
    ? (detail?.scope?.allowed_capabilities as unknown[]).map(String)
    : []

  const canRevoke = detail
    ? !['done', 'failed', 'revoked'].includes(detail.status)
    : false

  return (
    <Popover
      onOpenChange={next => {
        setOpen(next)

        if (next) {
          load()
        }
      }}
      open={open}
    >
      <PopoverTrigger asChild>
        <span
          aria-label={ticketId}
          className={cn(
            'inline-flex shrink-0 cursor-pointer items-center gap-1 rounded px-1 py-0.5',
            'text-(--ui-text-quaternary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-secondary)'
          )}
          onClick={event => event.stopPropagation()}
          role="button"
          tabIndex={0}
          title={ticketId}
        >
          <Status className={iconSize.xs} />
          <span className="font-mono text-[0.6rem] leading-none">{ticketId.slice(-4)}</span>
        </span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-2" side="right">
        <div className="flex items-center gap-2 px-1 pb-1.5">
          <Status className={iconSize.sm} />
          <span className="font-mono text-[0.7rem]">{ticketId}</span>
        </div>
        {error ? (
          <div className="px-1 py-1 text-[0.7rem] text-(--ui-red)">{error}</div>
        ) : null}
        {detail ? (
          <div className="flex flex-col gap-1 border-t border-(--ui-stroke-tertiary) pt-1.5">
            <div className="flex items-center gap-2 px-1 text-[0.68rem] text-(--ui-text-tertiary)">
              <Clock className={iconSize.xs} />
              <span>{t.console.ticketScope}</span>
              <span className="ml-auto truncate font-mono">{allowed.length ? allowed.join(' · ') : '—'}</span>
            </div>
            <div className="flex items-center gap-2 px-1 text-[0.68rem] text-(--ui-text-tertiary)">
              <ArchiveOff className={iconSize.xs} />
              <span>{t.console.ticketPolicy}</span>
              <span className="ml-auto truncate font-mono">{detail.policy_version || '—'}</span>
            </div>
            {canRevoke ? (
              <button
                aria-label={t.console.ticketRevoke}
                className="mt-1 flex items-center gap-2 rounded-md px-1 py-1 text-[0.7rem] text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background)"
                disabled={busy}
                onClick={revoke}
                title={t.console.ticketRevoke}
                type="button"
              >
                <X className={iconSize.xs} />
                <span>{t.console.ticketRevoke}</span>
              </button>
            ) : null}
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
