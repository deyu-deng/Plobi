import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { getAgentOffloads } from '@/plobi'
import type { OffloadRound } from '@/types/plobi'

import { externalHandleOf, groupRoundsByTarget, type JumpReason, jumpTargetFor, roundFacts } from './agent-offloads'
import { SidebarCount } from './chrome'

const JUMP_REASON_KEYS: Partial<Record<JumpReason, 'offloadsNoArtifact' | 'offloadsNoTransport'>> = {
  'no-artifact': 'offloadsNoArtifact',
  'no-jump-transport': 'offloadsNoTransport',
}

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
  const [rounds, setRounds] = useState<null | OffloadRound[]>(null)
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
              <Codicon name="rocket" />
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

function OffloadRoundRow({ round }: { round: OffloadRound }) {
  const { t } = useI18n()
  const c = t.console
  const plan = jumpTargetFor(round)
  const facts = roundFacts(round)
  const clickable = plan.kind === 'reveal'
  const [failed, setFailed] = useState(false)

  const parts = [
    facts.permissionFlag,
    facts.durationSeconds === null ? '' : `${facts.durationSeconds.toFixed(0)}s`,
    facts.costUsd === null
      ? `${c.offloadsCostUnknown}${facts.costStatus ? ` (${facts.costStatus})` : ''}`
      : `$${facts.costUsd}`,
  ].filter(Boolean)

  const reasonKey = plan.reason ? JUMP_REASON_KEYS[plan.reason] : undefined
  const reason = reasonKey ? c[reasonKey] : ''
  const handle = externalHandleOf(round)
  const title = [round.cwd, plan.kind === 'reveal' ? c.offloadsReveal : reason, round.failure].filter(Boolean).join(' · ')

  return (
    <button
      className={cn(
        'flex w-full flex-col gap-0.5 rounded-md px-2 py-1 text-left',
        clickable ? 'hover:bg-(--ui-bg-secondary)' : 'cursor-default opacity-70',
      )}
      disabled={!clickable}
      onClick={() => {
        if (clickable) {
          void window.plobiDesktop
            ?.openExternal?.(plan.url)
            .then(() => setFailed(false))
            .catch(() => setFailed(true))
        }
      }}
      title={title}
      type="button"
    >
      <span className="truncate text-[0.75rem]">{round.brief || round.cwd || '—'}</span>
      {handle && (
        <span className="truncate text-[0.65rem] text-(--ui-text-quaternary)" title={round.external_session_source || undefined}>
          {c.offloadsSessionId}
          {' '}
          {handle}
        </span>
      )}
      {round.failure && (
        <span className="truncate text-[0.68rem] text-(--ui-red)">{round.failure}</span>
      )}
      <span className="truncate text-[0.68rem] text-(--ui-text-tertiary)">
        {parts.join(' · ')}
        {failed ? ` · ${c.offloadsOpenFailed}` : ''}
        {!clickable && reason ? ` · ${reason}` : ''}
      </span>
    </button>
  )
}
