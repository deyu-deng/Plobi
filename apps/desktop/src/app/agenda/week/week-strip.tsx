import { useEffect, useMemo, useRef, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n, type Translations } from '@/i18n'
import { cn } from '@/lib/utils'
import type { AgendaConflict, AgendaEvent } from '@/types/plobi'

import {
  buildWeekView,
  mondayKeyOf,
  shiftWeek,
  weekDayKeys,
  weekTotal,
  type WeekView
} from './week-model'
import {
  createDayFetcher,
  type DayFetcher,
  fetchWeekConflicts,
  type WeekConflictState
} from './week-snapshots'

/**
 * WP-AGENDA-VIEW slice 1 — the week row on the desktop agenda board.
 *
 * What this is: a Monday-anchored row of seven local days over the events the board already
 * holds, with prev / this-week / next panning and a click that filters the list below.
 *
 * The shape is icon-first (用户 10-22：「少一点文字，多用矢量图标表达意思」): the pan controls
 * are glyph buttons, each day carries the kind of work it holds as glyphs, and the honesty
 * words a machine needs — 「冲突未取」/「已过」/ counts — stay in `aria-label` + `title` instead
 * of stacking three lines of text under every date.
 *
 * What this deliberately does **not** claim:
 * - It does not draw the vertical hour axis (the week calendar is the next cut).
 * - It does not compute conflicts. `conflictsByDay` comes from `/api/agenda/day` snapshots
 *   (`planning.py` `compute_event_overlaps`); a day without a snapshot says 「冲突未取」 rather
 *   than painting a confident zero.
 * - "Looks right on screen" is not something a unit test can decide — that half waits for the
 *   user (架构师 10-14 ②).
 */

export interface AgendaWeekStripProps {
  /** Day snapshot conflicts by `YYYY-MM-DD`. Absent key = not loaded (the UI must say so). */
  conflictsByDay?: Record<string, AgendaConflict[]>
  events: AgendaEvent[]
  /** Injectable transport for the seven snapshots; production uses `createDayFetcher()`. */
  fetchDay?: DayFetcher
  onPickDay: (dayKey: null | string) => void
  pickedDayKey: null | string
  todayKey: string
  /** Test escape hatch; production reads `t` from the provider. */
  translations?: Translations
}

function shortDate(dayKey: string): string {
  return dayKey.slice(5)
}

const KIND_ICON: Record<string, string> = {
  class: 'book',
  ddl: 'flag',
  meeting: 'people',
  task: 'check'
}

const KIND_COLOR: Record<string, string> = {
  class: 'text-sky-500',
  ddl: 'text-amber-500',
  meeting: 'text-violet-500',
  task: 'text-emerald-500'
}

export function AgendaWeekStrip({
  conflictsByDay,
  events,
  fetchDay,
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

  // C1 渐进：the columns paint from the rows we already hold, and the conflict layer fills in
  // per day. `requestSeq` is the staleness guard — panning twice quickly must not let the first
  // week's late answer overwrite the week now on screen.
  const [snapshots, setSnapshots] = useState<WeekConflictState>({
    conflictsByDay: {},
    loadedCount: 0
  })

  const [fallbackFetcher] = useState(createDayFetcher)
  const activeFetcher = fetchDay ?? fallbackFetcher
  const requestSeq = useRef(0)

  useEffect(() => {
    const keys = weekDayKeys(monday)

    if (keys.length === 0) {
      return
    }

    const seq = requestSeq.current + 1

    requestSeq.current = seq
    void fetchWeekConflicts(keys, activeFetcher).then(next => {
      if (requestSeq.current !== seq) {
        return
      }

      setSnapshots(next)
    })
  }, [activeFetcher, monday])

  // An explicit prop wins over what this instance fetched (callers may already hold a snapshot).
  const mergedConflicts = useMemo(
    () => ({ ...snapshots.conflictsByDay, ...conflictsByDay }),
    [conflictsByDay, snapshots.conflictsByDay]
  )

  const view: WeekView = useMemo(
    () => buildWeekView({ conflictsByDay: mergedConflicts, events, mondayKey: monday, todayKey }),
    [events, mergedConflicts, monday, todayKey]
  )

  const keys = weekDayKeys(monday)

  if (keys.length === 0) {
    // No parsable "today" (a clock-less test environment, a malformed row): render nothing
    // rather than a strip of empty columns.
    return null
  }

  const iconButton =
    'inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground disabled:opacity-35 disabled:hover:bg-transparent'

  return (
    <div
      aria-label={w.label}
      className="flex flex-col gap-1.5 border-b px-2 pb-2 pt-2"
      data-testid="agenda-week-strip"
      role="group"
    >
      <div className="flex items-center gap-1">
        <button aria-label={w.prevWeek} className={iconButton} data-testid="week-prev" onClick={() => setPanOffset(offset => offset - 1)} type="button">
          <Codicon name="chevron-left" size="0.95rem" />
        </button>
        <button
          aria-label={w.thisWeek}
          className={iconButton}
          data-testid="week-today"
          disabled={panOffset === 0}
          onClick={() => setPanOffset(0)}
          type="button"
        >
          <Codicon name="calendar" size="0.9rem" />
        </button>
        <button aria-label={w.nextWeek} className={iconButton} data-testid="week-next" onClick={() => setPanOffset(offset => offset + 1)} type="button">
          <Codicon name="chevron-right" size="0.95rem" />
        </button>
        <span className="ml-1 text-[0.68rem] tabular-nums text-muted-foreground">{w.range(keys[0], keys[keys.length - 1])}</span>
        <span
          className="ml-auto inline-flex items-center gap-1 text-[0.68rem] tabular-nums text-muted-foreground"
          data-testid="week-total"
          title={w.count(weekTotal(view))}
        >
          <Codicon name="calendar" size="0.75rem" />
          {weekTotal(view)}
        </span>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {view.columns.map(column => {
          const conflictText = !column.conflictKnown
            ? w.conflictsUnknown
            : column.conflictCount > 0
              ? w.conflicts(column.conflictCount)
              : w.noConflicts
          const label = [
            w.days[column.dayIndex],
            shortDate(column.dayKey),
            column.isToday ? w.today : '',
            w.dayCount(column.events.length + column.pending.length),
            column.pending.length > 0 ? w.pending(column.pending.length) : '',
            conflictText,
            column.isPast ? w.past : ''
          ]
            .filter(Boolean)
            .join(' · ')
          const marks = column.events.slice(0, 3)
          const rest = column.events.length - marks.length

          return (
            <button
              aria-current={column.isToday ? 'date' : undefined}
              aria-label={label}
              aria-pressed={pickedDayKey === column.dayKey}
              className={cn(
                'flex min-w-0 flex-col items-stretch gap-1 rounded-lg border px-1.5 pb-1.5 pt-1 text-left text-xs transition-colors',
                pickedDayKey === column.dayKey
                  ? 'border-primary/60 bg-primary/5'
                  : 'border-transparent hover:bg-(--ui-control-hover-background)',
                column.isPast && !column.isToday && 'opacity-45'
              )}
              data-conflict-state={!column.conflictKnown ? 'unknown' : column.conflictCount > 0 ? 'conflict' : 'clean'}
              data-count={column.events.length + column.pending.length}
              data-day={column.dayKey}
              data-past={column.isPast ? 'true' : undefined}
              data-pending={column.pending.length}
              data-testid="agenda-week-day"
              data-today={column.isToday ? 'true' : undefined}
              key={column.dayKey}
              onClick={() => onPickDay(pickedDayKey === column.dayKey ? null : column.dayKey)}
              title={label}
              type="button"
            >
              <span className="flex items-center justify-between gap-1">
                <span className={cn('text-[0.6rem] font-medium tracking-wide', column.isToday ? 'text-primary' : 'text-muted-foreground')}>
                  {w.days[column.dayIndex]}
                </span>
                {column.isToday ? (
                  <span className="rounded-full bg-primary/10 px-1.5 py-px text-[0.55rem] text-primary">{w.today}</span>
                ) : column.isPast ? (
                  <Codicon className="text-muted-foreground/40" name="history" size="0.65rem" />
                ) : null}
              </span>
              <span className={cn('text-sm font-semibold tabular-nums', column.isToday ? 'text-primary' : 'text-foreground/85')}>
                {shortDate(column.dayKey)}
              </span>
              <span aria-hidden="true" className="flex h-3 items-center gap-1">
                {marks.map(event => (
                  <Codicon
                    className={cn('shrink-0', KIND_COLOR[event.kind] ?? 'text-muted-foreground/60')}
                    key={event.id}
                    name={KIND_ICON[event.kind] ?? 'circle-filled'}
                    size="0.7rem"
                  />
                ))}
                {rest > 0 ? <span className="text-[0.6rem] text-muted-foreground">+{rest}</span> : null}
              </span>
              <span aria-hidden="true" className="flex h-3 items-center gap-1.5 text-[0.62rem] text-muted-foreground">
                {column.pending.length > 0 ? (
                  <span className="inline-flex items-center gap-0.5">
                    <Codicon name="bell-dot" size="0.7rem" />
                    {column.pending.length}
                  </span>
                ) : null}
                {column.conflictKnown && column.conflictCount > 0 ? (
                  <span className="inline-flex items-center gap-0.5 text-amber-500">
                    <Codicon name="warning" size="0.7rem" />
                    {column.conflictCount}
                  </span>
                ) : null}
                {!column.conflictKnown ? (
                  <span className="inline-flex items-center text-muted-foreground/45">
                    <Codicon name="circle-dashed" size="0.7rem" />
                  </span>
                ) : null}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
