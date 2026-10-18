import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { type Translations, useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { setAgendaEventCompleted } from '@/plobi'
import { $agendaEventIds, $completedEventIds, upsertAgendaEvent } from '@/store/agenda'
import { notify, notifyError } from '@/store/notifications'

import type { DayPlan } from './week-snapshots'

/**
 * 裁定 140 slice 3 — 「第二天打开桌面就看得见」.
 *
 * The evening plan (`/api/agenda/day` → `plan_items` + `plan_status`) used to have no destination:
 * DingTalk was demoted to an optional plugin (裁定 52.5) and the phone is parked (裁定 139), so a
 * plan generated at 20:00 was written to disk and shown nowhere. The user picked a third shape:
 * the desktop itself shows it the next morning, in the same slot as the agenda. This component is
 * that slot.
 *
 * Two things it deliberately does not do:
 * - It does not claim anything was pushed. `delivered` stays the backend's honest field and the
 *   「no notifier configured」 warning stays loud (R-048) — this layer renders a plan, not a receipt.
 * - It does not invent a header. A day with no plan returns `null`, so an empty 「明天的计划」 box
 *   never appears on a morning where nothing was generated.
 */

export interface DayPlanSectionProps {
  /** Already-localized day label from the board (今天 / 明天 / `YYYY-MM-DD`). */
  dayLabel: string
  plan: DayPlan | undefined
  /** Test escape hatch; production reads `t` from the provider. */
  translations?: Translations
}

function clock(iso: null | string): string {
  if (!iso || iso.length < 16) {
    return ''
  }

  return iso.slice(11, 16)
}

/**
 * 计划行指向的那条**事件**。`event_id` 是后端 `plobi/agenda/planning.py` `_plan_item_dict`
 * 这一格带出来的（WP-PLAN-COMPLETION-ACK 判据③）——没有它，界面上那句"做完了"无处可认，
 * 作业就会每晚被重排一次。取不到就说"这一行不能认"，**不猜**。
 *
 * 非空**不等于真有一条事件**（WP-PLAN-ACK-NO-FAKE-CHECK）：排程自造的 `l1:block-3`、
 * `routine:seed-lunch` 也是非空字符串，点下去后端直接 404。所以这一格只管"有没有写"，
 * "真不真"由 `$agendaEventIds` 判——两件事别揉进一个函数。
 */
function eventIdOf(item: object): null | string {
  const raw = (item as { event_id?: null | string }).event_id

  return typeof raw === 'string' && raw.length > 0 ? raw : null
}

function line(item: { end_at: null | string; start_at: string; title: string }): string {
  const start = clock(item.start_at)
  const end = clock(item.end_at)

  return [start ? (end ? `${start}–${end}` : start) : '', item.title].filter(Boolean).join(' ')
}

export function DayPlanSection({ dayLabel, plan, translations }: DayPlanSectionProps) {
  const { t } = useI18n()
  const p = (translations ?? t).agenda.plan
  const a = (translations ?? t).agenda
  const completed = useStore($completedEventIds)
  const knownEventIds = useStore($agendaEventIds)
  const [busyId, setBusyId] = useState<null | string>(null)

  if (!plan) {
    return null
  }

  async function ack(eventId: string, done: boolean) {
    setBusyId(eventId)

    try {
      // 服务端是唯一说了算的一方：认完把那一行原样写回缓存，界面不乐观猜。
      upsertAgendaEvent(await setAgendaEventCompleted(eventId, !done))
      notify({ message: !done ? a.acked : a.unacked })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div data-testid={`day-plan-${dayLabel}`} role="group">
      <div className="flex items-center gap-2 px-2 pb-1 pt-2">
        <span className="text-xs font-medium">{p.title(dayLabel)}</span>
        <span className="text-xs text-muted-foreground">
          {plan.status === 'pending' ? p.pending : (plan.status ?? p.statusUnknown)}
        </span>
      </div>
      {plan.items.length === 0 ? (
        <div className="px-2 pb-1 text-xs text-muted-foreground">{p.empty}</div>
      ) : (
        <ul className="flex flex-col gap-0.5 px-2 pb-1">
          {plan.items.map(item => {
            const eventId = eventIdOf(item)
            const done = eventId !== null && completed.has(eventId)
            // 服务端答过（哪怕答的是空一天）才谈得上"查无此行"；还没答过就报"记不了"
            // 等于把一次加载中的空窗说成一条事实——两头都不说。
            const answered = knownEventIds.size > 0
            const ackable = eventId !== null && knownEventIds.has(eventId)
            const unrecordable = eventId !== null && answered && !ackable

            return (
              <li className="flex items-start gap-1 text-xs" key={`${item.id}-${item.start_at}`}>
                <span className={cn('min-w-0 flex-1', done && 'text-muted-foreground line-through')}>{line(item)}</span>
                {done ? <span className="shrink-0 text-[0.62rem] text-muted-foreground">{a.donePill}</span> : null}
                {unrecordable ? (
                  <span className="shrink-0 text-[0.62rem] text-muted-foreground" data-testid={`unrecordable-${item.id}`}>
                    {p.unackable}
                  </span>
                ) : null}
                {ackable && eventId ? (
                  <button
                    aria-label={done ? a.uncomplete : a.complete}
                    className="shrink-0 cursor-pointer text-muted-foreground hover:text-foreground disabled:cursor-wait"
                    disabled={busyId === eventId}
                    onClick={() => void ack(eventId, done)}
                    title={done ? a.uncomplete : a.complete}
                    type="button"
                  >
                    <Codicon name={done ? 'history' : 'pass'} size="0.875rem" />
                  </button>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
