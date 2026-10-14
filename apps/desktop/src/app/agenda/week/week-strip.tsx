import { useMemo, useState } from 'react'

import { type Translations, useI18n } from '@/i18n'
import type { AgendaConflict, AgendaEvent } from '@/types/plobi'

import {
  buildWeekView,
  mondayKeyOf,
  shiftWeek,
  weekDayKeys,
  weekTotal,
  type WeekView
} from './week-model'

/**
 * WP-AGENDA-VIEW slice 1 — the week row on the desktop agenda board.
 *
 * What this is: a Monday-anchored row of seven local days over the events the board already
 * holds, with prev / this-week / next panning and a click that filters the list below. It adds
 * no surface (it lives inside `AgendaView`) and fires no request.
 *
 * What this deliberately does **not** claim:
 * - It does not draw the vertical hour axis yet (slice 2 — the axis math already exists in
 *   `src/app/console/right/timeline-layout.ts`, and the plan is to reuse it, not fork it).
 * - It does not compute conflicts. `conflictsByDay` comes from `/api/agenda/day` snapshots
 *   (`planning.py` `compute_event_overlaps`); a day without a snapshot says 「冲突未取」 rather
 *   than painting a confident zero. Loading those seven snapshots is also slice 2.
 * - "Looks right on screen" is not something a unit test can decide — that half waits for the
 *   user (架构师 10-14 ②).
 */

export interface AgendaWeekStripProps {
  /** Day snapshot conflicts by `YYYY-MM-DD`. Absent key = not loaded (the UI must say so). */
  conflictsByDay?: Record<string, AgendaConflict[]>
  events: AgendaEvent[]
  onPickDay: (dayKey: null | string) => void
  pickedDayKey: null | string
  todayKey: string
  /** Test escape hatch; production reads `t` from the provider. */
  translations?: Translations
}

function shortDate(dayKey: string): string {
  return dayKey.slice(5)
}

export function AgendaWeekStrip({
  conflictsByDay,
  events,
  onPickDay,
  pickedDayKey,
  todayKey,
  translations
}: AgendaWeekStripProps) {
  const { t: contextT } = useI18n()
  const w = (translations ?? contextT).agenda.week

  // Panning is relative to "this week", so the anchor is recomputed on every render: at
  // midnight rollover the strip follows the new day unless the user has panned away.
  const anchor = mondayKeyOf(todayKey)
  const [panOffset, setPanOffset] = useState(0)
  const monday = panOffset === 0 ? anchor : shiftWeek(anchor, panOffset)

  const view: WeekView = useMemo(
    () => buildWeekView({ conflictsByDay, events, mondayKey: monday, todayKey }),
    [conflictsByDay, events, monday, todayKey]
  )

  const keys = weekDayKeys(monday)

  if (keys.length === 0) {
    // No parsable "today" (a clock-less test environment, a malformed row): render nothing
    // rather than a strip of empty columns.
    return null
  }

  return (
    <div
      aria-label={w.label}
      className="flex flex-col gap-1 border-b px-2 pb-2 pt-2"
      data-testid="agenda-week-strip"
      role="group"
    >
      <div className="flex items-center gap-2">
        <button
          aria-label={w.prevWeek}
          className="rounded border px-2 py-0.5 text-xs"
          onClick={() => setPanOffset(offset => offset - 1)}
          type="button"
        >
          {w.prevWeek}
        </button>
        <button
          className="rounded border px-2 py-0.5 text-xs"
          disabled={panOffset === 0}
          onClick={() => setPanOffset(0)}
          type="button"
        >
          {w.thisWeek}
        </button>
        <button
          aria-label={w.nextWeek}
          className="rounded border px-2 py-0.5 text-xs"
          onClick={() => setPanOffset(offset => offset + 1)}
          type="button"
        >
          {w.nextWeek}
        </button>
        <span className="text-xs text-muted-foreground">{w.range(keys[0], keys[keys.length - 1])}</span>
        <span className="ml-auto text-xs text-muted-foreground">{w.count(weekTotal(view))}</span>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {view.columns.map(column => (
          <button
            aria-current={column.isToday ? 'date' : undefined}
            aria-pressed={pickedDayKey === column.dayKey}
            className={`flex flex-col items-start gap-0.5 rounded border px-1.5 py-1 text-left text-xs ${
              pickedDayKey === column.dayKey ? 'border-primary' : 'border-transparent'
            }`}
            data-day={column.dayKey}
            data-testid="agenda-week-day"
            key={column.dayKey}
            onClick={() => onPickDay(pickedDayKey === column.dayKey ? null : column.dayKey)}
            type="button"
          >
            <span className="font-medium">
              {/* Weekday word, never colour alone, carries "this is today". */}
              {w.days[column.dayIndex]}
              {column.isToday ? ` · ${w.today}` : ''}
            </span>
            <span className="text-muted-foreground">{shortDate(column.dayKey)}</span>
            <span>{w.dayCount(column.events.length + column.pending.length)}</span>
            {column.pending.length > 0 ? <span>{w.pending(column.pending.length)}</span> : null}
            <span className="text-muted-foreground">
              {column.conflictKnown
                ? column.conflictCount > 0
                  ? w.conflicts(column.conflictCount)
                  : w.noConflicts
                : w.conflictsUnknown}
            </span>
            {column.isPast ? <span className="text-muted-foreground">{w.past}</span> : null}
          </button>
        ))}
      </div>
    </div>
  )
}
