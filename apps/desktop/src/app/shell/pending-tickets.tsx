import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import type { DispatchTicket } from '@/app/chat/sidebar/ticket-badge'
import { LongTip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import {
  ArchiveOff, Bell, Check, CheckCircle2, Clock, GitBranch, iconSize, Mail,
  MessageSquareText, Pencil, Server, X
} from '@/lib/icons'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'
import {
  $pendingInbox,
  $pendingInboxError,
  $pendingInboxFailed,
  type PendingInboxItem
} from '@/store/pending-inbox'

import { MonitorEntryButton } from './monitor-entry-button'
import { useMonitorFace } from './monitor-faces'
import { MonitorEmpty, MonitorRow } from './monitor-panel'

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

/** 带前缀的动作：票的 accept/reject/comment，授权的 approve/reject/revoke。 */
type DecisionKind =
  | 'ticket:accept' | 'ticket:reject' | 'ticket:comment'
  | 'lesson:approve' | 'lesson:reject' | 'lesson:revoke'

/** 收件箱里的一件：形状住在 `store/pending-inbox.ts`（快照要活过这一面关着的时候）。 */
type InboxItem = PendingInboxItem

type LessonRow = {
  id: string
  status: string
  scope: string
  rule: string
  question: string
  user_words: string
  evidence: string
}

async function fetchPending(): Promise<InboxItem[]> {
  const [observed, reports, drafts, live, decisions] = await Promise.all([
    window.plobiDesktop.api<{
      data?: { tickets?: DispatchTicket[] }, error?: string, ok: boolean
    }>({ path: '/api/tickets?status=observed&leg=trigger&limit=50' }),
    window.plobiDesktop.api<{
      data?: { tickets?: Array<{ id: string, goal: string, leg: string, state: string, one_line: string, status: string }> },
      error?: string, ok: boolean
    }>({ path: '/api/tickets/escalations?limit=50' }),
    window.plobiDesktop.api<{
      data?: { lessons?: LessonRow[] }, error?: string, ok: boolean
    }>({ path: '/api/lessons?status=proposed&limit=50' }),
    window.plobiDesktop.api<{
      data?: { lessons?: LessonRow[] }, error?: string, ok: boolean
    }>({ path: '/api/lessons?limit=50' }),
    window.plobiDesktop.api<{
      data?: { decisions?: Array<{ ticket_id: string, kind: string }> }, error?: string, ok: boolean
    }>({ path: '/api/decisions?limit=500' })
  ])

  // 反馈闭环（裁定 234）：人表态过的票不再列为待办。点一次就消失，不再
  // 因为「只写账、不改票状态」而看起来像没生效（曾有人连点 13 次）。
  // 只按人决定账过滤，不改票、不越权；读不到决定账就当空（宁可多列一次）。
  const decided = new Set<string>(
    decisions?.ok
      ? (decisions.data?.decisions ?? []).map(d => d.ticket_id).filter(Boolean)
      : []
  )

  if (!observed?.ok) {
    throw new Error(observed?.error || 'load failed')
  }

  const escalated: InboxItem[] = reports?.ok
    ? (reports.data?.tickets ?? []).map(row => ({
      kind: 'ticket' as const,
      id: row.id, goal: row.goal, leg: row.leg, status: row.status,
      one_line: row.one_line, refs: {}, triggered_by: 'report'
    }) as unknown as InboxItem)
    : []

  const lessonItems: InboxItem[] = []

  if (drafts?.ok) {
    for (const row of drafts.data?.lessons ?? []) {
      lessonItems.push({
        kind: 'lesson', id: row.id, status: row.status, scope: row.scope,
        rule: row.rule, question: row.question, words: row.user_words || row.evidence
      })
    }
  }

  if (live?.ok) {
    for (const row of live.data?.lessons ?? []) {
      lessonItems.push({
        kind: 'lesson', id: row.id, status: row.status, scope: row.scope,
        rule: row.rule, question: row.question, words: row.user_words || row.evidence
      })
    }
  }

  const ticketItems: InboxItem[] = [...escalated, ...(observed.data?.tickets ?? []).map(
    ticket => ({ kind: 'ticket' as const, ...ticket } as InboxItem)
  )].filter(item => !decided.has(item.id)) as InboxItem[]

  return [...lessonItems, ...ticketItems]
}

/** 人点按钮的 lesson 动作：点击本身就是授权证据，不填 user_words（那是 Agent 那侧要的）。 */
function lessonDecide(id: string, action: 'approve' | 'reject' | 'revoke'): Promise<void> {
  return window.plobiDesktop
    .api<{ error?: string, ok: boolean }>({
      method: 'POST',
      path: `/api/lessons/${encodeURIComponent(id)}/${action}`
    })
    .then(body => {
      if (!body?.ok) {
        throw new Error(body?.error || 'request failed')
      }
    })
}

function decide(id: string, kind: DecisionKind, comment = ''): Promise<void> {
  if (kind.startsWith('lesson:')) {
    return lessonDecide(id, kind.slice('lesson:'.length) as 'approve' | 'reject' | 'revoke')
  }

  return window.plobiDesktop
    .api<{ error?: string, ok: boolean }>({
      body: { comment, kind: kind.slice('ticket:'.length) },
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
  const { t } = useI18n()
  const [commentOpen, setCommentOpen] = useState(false)
  const [commentText, setCommentText] = useState('')

  if (ticket.kind === 'lesson') {
    const draft = ticket.status === 'proposed'
    const Icon = draft ? Pencil : CheckCircle2
    const summary = ticket.rule || ticket.question || ticket.scope

    return (
      <MonitorRow
        actions={
          draft ? (
            <>
              <button
                aria-label={t.console.inboxLessonApprove}
                className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
                disabled={busy}
                onClick={() => onDecide(ticket.id, 'lesson:approve')}
                title={t.console.inboxLessonApprove}
                type="button"
              >
                <Check className={iconSize.xs} />
              </button>
              <button
                aria-label={t.console.inboxLessonReject}
                className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
                disabled={busy}
                onClick={() => onDecide(ticket.id, 'lesson:reject')}
                title={t.console.inboxLessonReject}
                type="button"
              >
                <X className={iconSize.xs} />
              </button>
            </>
          ) : (
            <button
              aria-label={t.console.inboxLessonRevoke}
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'lesson:revoke')}
              title={t.console.inboxLessonRevoke}
              type="button"
            >
              <ArchiveOff className={iconSize.xs} />
            </button>
          )
        }
        icon={<Icon aria-hidden />}
        label={
          <LongTip label={ticket.words || summary}>
            <span>{summary}</span>
          </LongTip>
        }
      />
    )
  }

  const Source = sourceIcon(ticket)
  const summary = ticket.one_line || ticket.refs?.prompt_excerpt || ticket.goal

  const submitComment = () => {
    const text = commentText.trim()

    if (!text) {
      return
    }

    onDecide(ticket.id, 'ticket:comment', text)
    setCommentText('')
    setCommentOpen(false)
  }

  /* 评论展开时那格输入框照旧：长在行的下面，不占行内的地方。 */
  return (
    <div className="flex flex-col gap-1">
      <MonitorRow
        actions={
          <>
            <button
              aria-label={t.console.inboxAccept}
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'ticket:accept')}
              title={t.console.inboxAccept}
              type="button"
            >
              <Check className={iconSize.xs} />
            </button>
            <button
              aria-label={t.console.inboxReject}
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'ticket:reject')}
              title={t.console.inboxReject}
              type="button"
            >
              <X className={iconSize.xs} />
            </button>
            <button
              aria-label={t.console.inboxComment}
              className={cn(
                'grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)',
                commentOpen && 'bg-(--ui-control-hover-background)'
              )}
              disabled={busy}
              onClick={() => setCommentOpen(!commentOpen)}
              title={t.console.inboxComment}
              type="button"
            >
              <MessageSquareText className={iconSize.xs} />
            </button>
          </>
        }
        icon={<Source aria-hidden />}
        label={
          <LongTip label={ticket.refs?.prompt || ticket.goal}>
            <span>{summary}</span>
          </LongTip>
        }
      />
      {commentOpen ? (
        <div className="flex items-center gap-1 px-1">
          <input
            className="min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-transparent px-1.5 py-0.5 text-xs outline-none"
            onChange={event => setCommentText(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                submitComment()
              }
            }}
            placeholder={t.console.inboxCommentHint}
            type="text"
            value={commentText}
          />
          <button
            aria-label={t.console.inboxCommentSubmit}
            className="shrink-0 rounded px-1.5 py-0.5 text-xs hover:bg-(--ui-control-hover-background)"
            disabled={busy || !commentText.trim()}
            onClick={submitComment}
            title={t.console.inboxCommentSubmit}
            type="button"
          >
            发送
          </button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * 「待放行」那一面的**内容**（只有内容 —— 壳由 `monitor-region.tsx` 那一张卡统一画）。
 *
 * 11-19 17:0x 统一成那一张卡：这一档**只把 Radix Popover 那层壳去掉**，装载
 * （5 秒轮询）、接纳／拒绝／撤回／评论、错误显示一个字没改。
 */
export function PendingTicketsFace() {
  const { t } = useI18n()
  const tickets = useStore($pendingInbox)
  const failed = useStore($pendingInboxFailed)
  const error = useStore($pendingInboxError)
  const [busy, setBusy] = useState('')

  // 刚展开时立刻刷一次（轮询最长要等 5 秒）。
  useEffect(() => {
    void loadInbox()
  }, [])

  const decideOne = (id: string, kind: DecisionKind, comment = '') => {
    setBusy(id)
    $pendingInboxError.set('')
    void decide(id, kind, comment)
      .then(loadInbox)
      .catch((reason: unknown) => $pendingInboxError.set(String(reason)))
      .finally(() => setBusy(''))
  }

  return (
    <>
      {failed ? (
        <div className="text-xs text-(--ui-red)">{t.surface.pendingTicketsFailed}</div>
      ) : tickets.length === 0 ? (
        <MonitorEmpty />
      ) : (
        <div className="flex flex-col gap-1.5">
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
        <div className="text-xs text-(--ui-red)">{error}</div>
      ) : null}
    </>
  )
}

/**
 * 抬头右上角那一族里的「待放行」**入口**（用户 11-10：「和档案一个形态，复用一个组件」）。
 *
 * 形状与行为都在统一件 `monitor-entry-button.tsx` 里；这一档只管**角标上那个数**
 * —— 所以轮询住在这里：这一枚入口一直挂在抬头上面，面板关着时那个数也准。
 */
export function PendingTicketsButton() {
  usePendingInboxPolling()
  const face = useMonitorFace('pending')

  return <MonitorEntryButton face={face} />
}

/** 轮询：角标上那个数要一直看得见，面板开不开都在刷。 */
function usePendingInboxPolling(): void {
  const load = useCallback(loadInbox, [])

  useEffect(() => {
    void load()
    const timer = setInterval(() => { void load() }, 5000)

    return () => clearInterval(timer)
  }, [load])
}

/** 拉一次清单，写进快照。 */
async function loadInbox(): Promise<void> {
  try {
    $pendingInbox.set(await fetchPending())
    $pendingInboxFailed.set(false)
  } catch {
    $pendingInboxFailed.set(true)
  }
}
