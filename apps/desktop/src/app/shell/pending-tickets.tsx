import { useCallback, useEffect, useState } from 'react'

import type { DispatchTicket } from '@/app/chat/sidebar/ticket-badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import {
  ArchiveOff, Bell, Check, CheckCircle2, Clock, GitBranch, iconSize, Mail,
  MessageSquareText, Pencil, Server, X
} from '@/lib/icons'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

import {
  MonitorEmpty, MonitorHeaderTitle, MonitorIcon,
  MonitorPanel, MonitorRow
} from './monitor-panel'
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

/** 带前缀的动作：票的 accept/reject/comment，授权的 approve/reject/revoke。 */
type DecisionKind =
  | 'ticket:accept' | 'ticket:reject' | 'ticket:comment'
  | 'lesson:approve' | 'lesson:reject' | 'lesson:revoke'

/** 收件箱里的一件：票（派活/汇报）或授权 lesson（草稿/已生效）。 */
type InboxItem =
  | (DispatchTicket & { kind: 'ticket', one_line?: string })
  | {
    kind: 'lesson'
    id: string
    status: string
    scope: string
    rule: string
    question: string
    words: string
  }

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
  const [observed, reports, drafts, live] = await Promise.all([
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
    }>({ path: '/api/lessons?limit=50' })
  ])

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

  return [
    ...lessonItems,
    ...escalated,
    ...(observed.data?.tickets ?? []).map(
      ticket => ({ kind: 'ticket' as const, ...ticket } as InboxItem)
    )
  ]
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
                aria-label="批准授权"
                className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
                disabled={busy}
                onClick={() => onDecide(ticket.id, 'lesson:approve')}
                title="批准授权"
                type="button"
              >
                <Check className={iconSize.xs} />
              </button>
              <button
                aria-label="驳回授权"
                className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
                disabled={busy}
                onClick={() => onDecide(ticket.id, 'lesson:reject')}
                title="驳回授权"
                type="button"
              >
                <X className={iconSize.xs} />
              </button>
            </>
          ) : (
            <button
              aria-label="撤回授权"
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'lesson:revoke')}
              title="撤回授权"
              type="button"
            >
              <ArchiveOff className={iconSize.xs} />
            </button>
          )
        }
        icon={<Icon aria-hidden />}
        label={<span title={ticket.words || summary}>{summary}</span>}
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
              aria-label="接纳"
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'ticket:accept')}
              title="接纳"
              type="button"
            >
              <Check className={iconSize.xs} />
            </button>
            <button
              aria-label="拒绝"
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => onDecide(ticket.id, 'ticket:reject')}
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
          </>
        }
        icon={<Source aria-hidden />}
        label={<span title={ticket.refs?.prompt || ticket.goal}>{summary}</span>}
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
            placeholder="说清楚为什么，让 Agent 理解后再记下来"
            type="text"
            value={commentText}
          />
          <button
            aria-label="提交评论"
            className="shrink-0 rounded px-1.5 py-0.5 text-xs hover:bg-(--ui-control-hover-background)"
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

/**
 * 抬头右上角那一族里的「待放行」入口（入口那颗按钮的形状归统一入口那把刀）。
 *
 * 11-19 视觉规格迁移：**容器**换成统一组件 `monitor-panel.tsx`（Qoder 任务监控那一把尺）——
 * 浮层 280px、行 28px、图标托 24px、字 12px、零描边、hover 才出底色。
 * 装载（5 秒轮询）、接纳／拒绝／撤回／评论、错误显示一个字没改。
 */
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
      <PopoverContent
        align="end"
        // 这层壳只管定位（Radix 的碰撞避让、点外面关掉），**不画皮**：
        // 皮由下面那块统一容器画，所以壳要透明、无描边、无内边距。
        // `[&>span]:hidden` 藏掉 Radix 自带的那枚箭头（Qoder 那个浮层没有箭头）。
        className="w-[280px] border-0 bg-transparent p-0 [&>span]:hidden"
        side="bottom"
      >
        <MonitorPanel
          // 壳里再套一层 `fixed` 会让 Radix 量不到高度（浮层会掉出窗口下沿），
          // 所以改成 `relative`：定位仍归 Radix，皮仍是浮层那一套。
          className="relative"
          header={
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <MonitorIcon>
                <Bell aria-hidden />
              </MonitorIcon>
              <MonitorHeaderTitle>{t.surface.pendingTickets}</MonitorHeaderTitle>
              <span className="ml-auto text-xs tabular-nums text-(--ui-text-tertiary)">
                {tickets.length}
              </span>
            </div>
          }
          mode="float"
        >
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
        </MonitorPanel>
      </PopoverContent>
    </Popover>
  )
}
