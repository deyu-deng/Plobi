import { type Translations, useI18n } from '@/i18n'

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

function line(item: { end_at: null | string; start_at: string; title: string }): string {
  const start = clock(item.start_at)
  const end = clock(item.end_at)

  return [start ? (end ? `${start}–${end}` : start) : '', item.title].filter(Boolean).join(' ')
}

export function DayPlanSection({ dayLabel, plan, translations }: DayPlanSectionProps) {
  const { t } = useI18n()
  const p = (translations ?? t).agenda.plan

  if (!plan) {
    return null
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
          {plan.items.map(item => (
            <li className="text-xs" key={`${item.id}-${item.start_at}`}>
              {line(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
