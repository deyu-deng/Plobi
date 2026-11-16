import { useCallback, useEffect, useState } from 'react'

import type { DispatchTicket } from '@/app/chat/sidebar/ticket-badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import {
  Bell, Check, Clock, GitBranch, iconSize, Mail, MessageSquareText, Server, X
} from '@/lib/icons'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { TopBarIconButton } from './top-bar-icon-button'

/**
 * 对话卡抬头右上角、档案图标旁边那一枚（用户 11-10：「和档案一个形态，复用一个组件」）。
 *
 * 外部事件默认 observe（裁定 215 #35）：webhook 只落一张「本来会接」的票，
 * 不启动 Agent。这里就是人看见并放行的唯一入口；清单里来源／状态／动作
 * 尽量用矢量图标说话，只留一行摘要文字。
 */
function sourceIcon(ticket: DispatchTicket): IconComponent {
  const key = `${ticket.triggered_by} ${ticket.refs?.event_type ?? ''} ${ticket.goal}`.toLowerCase()

  if (/github|pull_request|\bpr\b|issue/.test(key)) {return GitBranch}

  if (/ci|build|workflow|pipeline/.test(key)) {return Server}

  if (/mail|email|smtp/.test(key)) {return Mail}

  if (/chat|message|wechat|dingtalk|group/.test(key)) {return MessageSquareText}

  if (/cron|schedule|timer|calendar/.test(key)) {return Clock}

  return Bell
}

type InboxItem = DispatchTicket & { one_line?: string }
type DecisionKind = 'accept' | 'reject' | 'comment'

async function fetchPending(): Promise<InboxItem[]> {
  const [observed, reports] = await Promise.all([
    window.plobiDesktop.api<{
      data?: { tickets?: DispatchTicket[] }, error?: string, ok: boolean
    }>({ path: '/api/tickets?status=observed&leg=trigger&limit=50' }),
    window.plobiDesktop.api<{
      data?: { tickets?: Array<{ id: string, goal: string, leg: string, state: string, one_line: string, status: string }> },
      error?: string, ok: boolean
    }>({ path: '/api/tickets/escalations?limit=50' })
  ])

  if (!observed?.ok) {
    throw new Error(observed?.error || 'load failed')
  }

  const escalated: InboxItem[] = reports?.ok
    ? (reports.data?.tickets ?? []).map(row => ({
      id: row.id, goal: row.goal, leg: row.leg, status: row.status,
      one_line: row.one_line, refs: {}, triggered_by: 'report'
    }) as unknown as InboxItem)
    : []

  return [...escalated, ...(observed.data?.tickets ?? [])]
}

function decide(id: string, kind: DecisionKind, comment = ''): Promise<void> {
  return window.plobiDesktop
    .api<{ error?: string, ok: boolean }>({
      body: { comment, kind },
      method: 'POST',
      path: `/api/tickets/${encodeURIComponent(id)}/decision`
    })
    .then(body => {
      if (!body?.ok) {
        throw new Error(body?.error || 'request failed')
      }
    })
}

function PendingRow({
  busy, onDecide, ticket
}: {
  busy: boolean
  onDecide: (id: string, kind: DecisionKind, comment?: string) => void
  ticket: InboxItem
}) {
  const [commentOpen, setCommentOpen] = useState(false)
  const [commentText, setCommentText] = useState('')
  const Source = sourceIcon(ticket)
  const summary = ticket.one_line || ticket.refs?.prompt_excerpt || ticket.goal

  const submitComment = () => {
    const text = commentText.trim()
    if (!text) {
      return
    }
    onDecide(ticket.id, 'comment', text)
    setCommentText('')
    setCommentOpen(false)
  }

  return (
    <div className="flex flex-col gap-1 rounded-md border border-(--ui-stroke-tertiary) px-1.5 py-1">
      <div className="flex items-center gap-1.5">
        <Source aria-hidden className={cn(iconSize.sm, 'shrink-0 text-(--ui-text-tertiary)')} />
        <span className="min-w-0 flex-1 truncate text-[0.7rem]" title={ticket.refs?.prompt || ticket.goal}>
          {summary}
        </span>
        <Clock aria-hidden className={cn(iconSize.xs, 'shrink-0 text-(--ui-text-quaternary)')} />
        <button
          aria-label="接纳"
          className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
          disabled={busy}
          onClick={() => onDecide(ticket.id, 'accept')}
          title="接纳"
          type="button"
        >
          <Check className={iconSize.xs} />
        </button>
        <button
          aria-label="拒绝"
          className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
          disabled={busy}
          onClick={() => onDecide(ticket.id, 'reject')}
          title="拒绝"
          type="button"
        >
          <X className={iconSize.xs} />
        </button>
        <button
          aria-label="评论"
          className={cn(
            'grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)',
            commentOpen && 'bg-(--ui-control-hover-background)'
          )}
          disabled={busy}
          onClick={() => setCommentOpen(!commentOpen)}
          title="评论"
          type="button"
        >
          <MessageSquareText className={iconSize.xs} />
        </button>
      </div>
      {commentOpen ? (
        <div className="flex items-center gap-1 px-0.5">
          <input
            className="min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-transparent px-1.5 py-0.5 text-[0.68rem] outline-none"
            onChange={event => setCommentText(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                submitComment()
              }
            }}
            placeholder="说清楚为什么，让 Agent 理解后再记下来"
            type="text"
            value={commentText}
          />
          <button
            aria-label="提交评论"
            className="shrink-0 rounded px-1.5 py-0.5 text-[0.68rem] hover:bg-(--ui-control-hover-background)"
            disabled={busy || !commentText.trim()}
            onClick={submitComment}
            title="提交评论"
            type="button"
          >
            发送
          </button>
        </div>
      ) : null}
    </div>
  )
}

export function PendingTicketsButton() {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [tickets, setTickets] = useState<InboxItem[]>([])
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setTickets(await fetchPending())
      setFailed(false)
    } catch {
      setFailed(true)
    }
  }, [])

  // 图标上的角标要一直看得见，所以不开面板也轮询；面板打开时立刻刷一次。
  useEffect(() => {
    void load()
    const timer = setInterval(() => { void load() }, 5000)

    return () => clearInterval(timer)
  }, [load])

  const decideOne = (id: string, kind: DecisionKind, comment = '') => {
    setBusy(id)
    setError('')
    void decide(id, kind, comment)
      .then(load)
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setBusy(''))
  }

  return (
    <Popover
      onOpenChange={next => {
        setOpen(next)

        if (next) {
          void load()
        }
      }}
      open={open}
    >
      <PopoverTrigger asChild>
        <TopBarIconButton
          count={tickets.length}
          icon="inbox"
          label={t.surface.pendingTickets}
          pressed={open}
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-2" side="bottom">
        <div className="flex items-center gap-2 px-1 pb-1.5">
          <Bell className={iconSize.sm} />
          <span className="text-[0.75rem] font-medium">{t.surface.pendingTickets}</span>
          <span className="ml-auto font-mono text-[0.68rem] text-(--ui-text-tertiary)">
            {tickets.length}
          </span>
        </div>
        {failed ? (
          <div className="px-1 py-2 text-[0.7rem] text-(--ui-red)">{t.surface.pendingTicketsFailed}</div>
        ) : tickets.length === 0 ? (
          <div className="px-1 py-2 text-[0.7rem] text-(--ui-text-tertiary)">{t.surface.pendingTicketsEmpty}</div>
        ) : (
          <div className="flex flex-col gap-1">
            {tickets.map(ticket => (
              <PendingRow
                busy={busy === ticket.id}
                key={ticket.id}
                onDecide={decideOne}
                ticket={ticket}
              />
            ))}
          </div>
        )}
        {error ? (
          <div className="px-1 pt-1 text-[0.68rem] text-(--ui-red)">{error}</div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
