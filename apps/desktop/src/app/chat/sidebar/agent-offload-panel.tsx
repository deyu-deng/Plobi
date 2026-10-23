import { useEffect, useState } from 'react'

import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { getAgentOffloads, offloadResume } from '@/plobi'

import { externalHandleOf, groupRoundsByTarget, jumpTargetFor, roundFacts } from './agent-offloads'
import type { OffloadRoundWithReply } from './agent-offloads'
import { SidebarCount } from './chrome'
import { OffloadVendorAvatar } from './offload-vendor-icon'

/**
 * 点进一个 L2 时，左栏这一格换成**它把活外包给了谁**（WP-L2-OUTSOURCE-ENTRY 入口面③）。
 *
 * 数据来自 `GET /api/agents/:id/offloads`（按目录归属，见 `plobi/console/router.py` 的
 * `agent_offloads`）。一行点下去能到哪儿由 `jumpTargetFor()` 决定——跳不进外部软件自己
 * 那条会话时就退成"打开这一轮落盘的东西"，再没有就把这行标成不可点并说清为什么。
 */
export function AgentOffloadPanel({ agentId, onBack }: { agentId: string, onBack?: () => void }) {
  const { t } = useI18n()
  const c = t.console
  const [rounds, setRounds] = useState<null | OffloadRoundWithReply[]>(null)
  const [note, setNote] = useState('')
  const [openTarget, setOpenTarget] = useState<null | string>(null)

  useEffect(() => {
    let cancelled = false

    setRounds(null)
    getAgentOffloads(agentId)
      .then(body => {
        if (!cancelled) {
          setRounds(body?.data?.rounds ?? [])
          setNote(body?.data?.unattributed_reason ?? '')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRounds([])
          setNote(c.offloadsLoadFailed)
        }
      })

    return () => {
      cancelled = true
    }
  }, [agentId, c.offloadsLoadFailed])

  if (rounds === null) {
    return <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">{c.offloadsLoading}</div>
  }

  const groups = groupRoundsByTarget(rounds)

  return (
    <div className="flex flex-col gap-px">
      <div className="flex items-center gap-1 px-1">
        <span className="min-w-0 flex-1 truncate text-[0.7rem] uppercase text-(--ui-text-tertiary)">
          {c.offloadsTitle}
        </span>
        {onBack && (
          <button
            className="shrink-0 text-[0.7rem] text-(--ui-text-tertiary) hover:text-(--ui-text)"
            onClick={onBack}
            type="button"
          >
            {c.offloadsBackToList}
          </button>
        )}
      </div>

      {groups.length === 0 && (
        <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
          {note || c.offloadsEmpty}
        </div>
      )}

      {groups.map(group => {
        const open = openTarget === group.target

        return (
          <div key={group.target}>
            <button
              className="group/off flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[0.8rem] hover:bg-(--ui-bg-secondary)"
              onClick={() => setOpenTarget(open ? null : group.target)}
              title={group.target}
              type="button"
            >
              <OffloadVendorAvatar vendor={group.target} />
              <span className="min-w-0 flex-1 truncate">{group.target}</span>
              <SidebarCount>{group.rounds.length}</SidebarCount>
              <DisclosureCaret className="shrink-0 text-(--ui-text-tertiary)" open={open} />
            </button>

            {open && (
              <div className="flex flex-col gap-px pl-5">
                {group.rounds.map(round => (
                  <OffloadRoundRow key={`${round.ts}-${round.cwd}`} round={round} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function OffloadRoundRow({ round }: { round: OffloadRoundWithReply }) {
  const { t } = useI18n()
  const c = t.console
  const plan = jumpTargetFor(round)
  const facts = roundFacts(round)
  const clickable = plan.kind !== 'none'
  const [error, setError] = useState('')

  const parts = [
    facts.permissionFlag,
    facts.durationSeconds === null ? '' : `${facts.durationSeconds.toFixed(0)}s`,
    facts.costUsd === null
      ? `${c.offloadsCostUnknown}${facts.costStatus ? ` (${facts.costStatus})` : ''}`
      : `$${facts.costUsd}`,
  ].filter(Boolean)

  const reason = plan.reason ? c.offloadsNoArtifact : ''
  const handle = externalHandleOf(round)
  const action = plan.kind === 'resume' ? c.offloadsJump : plan.kind === 'reveal' ? c.offloadsReveal : reason
  const title = [round.cwd, action, round.failure].filter(Boolean).join(' · ')

  const run = () => {
    if (plan.kind === 'resume' && plan.resume) {
      const { agent, sessionId } = plan.resume

      void offloadResume(agent, sessionId)
        .then(() => setError(''))
        .catch((reason: unknown) => setError(jumpFailureText(reason, c.offloadsJumpFailed)))
    } else if (plan.kind === 'reveal') {
      void window.plobiDesktop
        ?.openExternal?.(plan.url)
        .then(() => setError(''))
        .catch(() => setError(c.offloadsOpenFailed))
    }
  }

  return (
    <button
      className={cn(
        'flex w-full flex-col gap-0.5 rounded-md px-2 py-1 text-left',
        clickable ? 'hover:bg-(--ui-bg-secondary)' : 'cursor-default opacity-70',
      )}
      disabled={!clickable}
      onClick={run}
      title={title}
      type="button"
    >
      <span className="truncate text-[0.75rem]">{round.brief || round.cwd || '—'}</span>
      {round.reply && (
        <span className="line-clamp-3 text-[0.7rem] leading-snug text-(--ui-text-secondary)">
          {c.offloadsReply}
          {round.reply}
        </span>
      )}
      {round.failure && (
        <span className="truncate text-[0.68rem] text-(--ui-red)">{round.failure}</span>
      )}
      {handle && (
        <span className="truncate text-[0.65rem] text-(--ui-text-quaternary)" title={round.external_session_source || undefined}>
          {c.offloadsSessionId}
          {' '}
          {handle}
        </span>
      )}
      <span className="truncate text-[0.68rem] text-(--ui-text-tertiary)">
        {parts.join(' · ')}
        {error ? ` · ${error}` : ''}
        {!clickable && reason ? ` · ${reason}` : ''}
      </span>
    </button>
  )
}

/**
 * 后端那四种失败各有一句话（判据 4），但 `fetchJson` 对非 2xx 是 reject，消息形如
 * `400: {"ok":false,"error":"……"}`。这里把那句话从外壳里取出来，取不到就截断原样，
 * 不把 JSON 甩在侧栏那一行上。
 */
function jumpFailureText(reason: unknown, fallback: string): string {
  const raw = reason instanceof Error ? reason.message : String(reason ?? '')
  const start = raw.indexOf('{')

  if (start >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(start)) as { error?: unknown }

      if (typeof parsed.error === 'string' && parsed.error) {
        return parsed.error
      }
    } catch {
      // 不是 JSON 就当原文处理
    }
  }

  return raw ? `${fallback}${raw.slice(0, 120)}` : fallback
}
