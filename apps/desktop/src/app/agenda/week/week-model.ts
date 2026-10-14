import type { AgendaConflict, AgendaEvent } from '@/types/plobi'

/**
 * WP-AGENDA-VIEW — the week layer for the desktop agenda board.
 *
 * Pure: no fetching, no DOM, no clock. Everything that touches the wall clock arrives
 * as an argument (`todayKey`), which is what makes this testable without a browser.
 *
 * Two rules this file exists to enforce, both inherited from the phone side so the two
 * surfaces can never drift into "两套算法":
 *
 * 1. **A week is Monday-anchored and device-local** (用户 10-09 拍的 D2，与鸿蒙
 *    `entry/src/main/ets/service/WeekView.ets` 同口径). Day keys are `YYYY-MM-DD` calendar
 *    days built from local `Date` components — never `toISOString().slice(0, 10)`, which is a
 *    UTC date and rolls over at 16:00 China time, and never `new Date('YYYY-MM-DD')`, which
 *    JS parses as UTC midnight (裁定 33.5).
 * 2. **Conflicts are carried, never computed here** (`Docs/specs/handset-app.md` §3.1
 *    「别自己再算一遍冲突」; `plobi/agenda/planning.py` `compute_event_overlaps` is the single
 *    implementation — half-open `[start, end)`, a missing `end_at` is a point and makes none).
 *    So a column reports `conflictKnown` plus the count from the day snapshot the caller
 *    already fetched; when that day has no snapshot loaded, we say "not loaded" instead of
 *    silently painting zero.
 */

export const DAYS_PER_WEEK = 7

const DAY_KEY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

export interface WeekColumn {
  /** How many overlap pairs the backend reported for this day. Only meaningful with `conflictKnown`. */
  conflictCount: number
  /** False when no day snapshot has been loaded for this column — the UI must then say so, not show 0. */
  conflictKnown: boolean
  /** 0 = Monday … 6 = Sunday. */
  dayIndex: number
  dayKey: string
  /** Everything on this day that is not awaiting confirmation, sorted by `start_at`. */
  events: AgendaEvent[]
  isPast: boolean
  isToday: boolean
  /** `status === 'pending'` — the phone side keeps these in the same column, so do we. */
  pending: AgendaEvent[]
}

export interface WeekView {
  columns: WeekColumn[]
  /** Events whose day falls outside the visible week. Counted, never hidden. */
  droppedCount: number
  /** Events whose `start_at` carries no parsable local day. Counted, never hidden. */
  invalidCount: number
  mondayKey: string
}

export interface WeekBuildInput {
  conflictsByDay?: Record<string, AgendaConflict[]>
  events: AgendaEvent[]
  mondayKey: string
  todayKey: string
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, '0')
}

/** Local-calendar-day key for a `Date`. Component-based, so DST and offsets cannot shift it. */
export function dayKeyFromDate(date: Date): string {
  return `${pad(date.getFullYear(), 4)}-${pad(date.getMonth() + 1, 2)}-${pad(date.getDate(), 2)}`
}

/** `YYYY-MM-DD` → a local-midnight `Date`; anything else → `null` (we do not guess). */
export function dayKeyToDate(dayKey: string): null | Date {
  const match = DAY_KEY_PATTERN.exec(dayKey)

  if (!match) {
    return null
  }

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(year, month - 1, day)

  // Reject 2026-02-30 and friends: the constructor rolls over, and a rolled-over key
  // would silently move an event to a different column.
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
    return null
  }

  return date
}

/**
 * The local calendar day an event sits on.
 *
 * Backend rows carry naive local ISO (`2026-10-14T09:00:00`); when a row does arrive with an
 * offset, we resolve it against the machine's zone instead of slicing the string, because a
 * slice would print the UTC day.
 */
export function eventDayKey(startAt: null | string | undefined): string {
  if (!startAt) {
    return ''
  }

  if (DAY_KEY_PATTERN.test(startAt.slice(0, 10)) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(startAt)) {
    return startAt.slice(0, 10)
  }

  const parsed = new Date(startAt)

  return Number.isNaN(parsed.getTime()) ? '' : dayKeyFromDate(parsed)
}

export function addDays(dayKey: string, delta: number): string {
  const date = dayKeyToDate(dayKey)

  if (!date) {
    return ''
  }

  return dayKeyFromDate(new Date(date.getFullYear(), date.getMonth(), date.getDate() + delta))
}

/** Monday anchor for any local day key; `''` for anything we cannot parse. */
export function mondayKeyOf(dayKey: string): string {
  const date = dayKeyToDate(dayKey)

  if (!date) {
    return ''
  }

  // getDay(): 0 = Sunday … 6 = Saturday → shift so Monday is 0.
  return addDays(dayKey, -((date.getDay() + 6) % 7))
}

/** The seven local day keys of the week starting at `mondayKey`; `[]` for a bad anchor. */
export function weekDayKeys(mondayKey: string): string[] {
  if (!dayKeyToDate(mondayKey)) {
    return []
  }

  const keys: string[] = []

  for (let index = 0; index < DAYS_PER_WEEK; index += 1) {
    keys.push(addDays(mondayKey, index))
  }

  return keys
}

/** Move a whole week back or forward (`delta` may be negative). */
export function shiftWeek(mondayKey: string, delta: number): string {
  return addDays(mondayKey, delta * DAYS_PER_WEEK)
}

function byStartAsc(left: AgendaEvent, right: AgendaEvent): number {
  return left.start_at.localeCompare(right.start_at)
}

/**
 * Bucket a flat event list into the seven columns of one week.
 *
 * Nothing is truncated here: `droppedCount` / `invalidCount` exist so the caller can *say*
 * how much fell outside the week instead of quietly dropping it — the same discipline the
 * phone-side bucketing uses.
 */
export function buildWeekView(input: WeekBuildInput): WeekView {
  const keys = weekDayKeys(input.mondayKey)

  const columns: WeekColumn[] = keys.map((dayKey, dayIndex) => ({
    conflictCount: (input.conflictsByDay?.[dayKey] ?? []).length,
    conflictKnown: Boolean(input.conflictsByDay?.[dayKey]),
    dayIndex,
    dayKey,
    events: [],
    isPast: Boolean(input.todayKey) && dayKey < input.todayKey,
    isToday: Boolean(input.todayKey) && dayKey === input.todayKey,
    pending: []
  }))

  const indexOf = new Map<string, number>(keys.map((key, position) => [key, position]))

  let droppedCount = 0
  let invalidCount = 0

  for (const event of input.events) {
    const dayKey = eventDayKey(event.start_at)
    const position = indexOf.get(dayKey)

    if (!dayKey) {
      invalidCount += 1

      continue
    }

    if (position === undefined) {
      droppedCount += 1

      continue
    }

    const column = columns[position]

    if (event.status === 'pending') {
      column.pending.push(event)
    } else {
      column.events.push(event)
    }
  }

  for (const column of columns) {
    column.events.sort(byStartAsc)
    column.pending.sort(byStartAsc)
  }

  return { columns, droppedCount, invalidCount, mondayKey: input.mondayKey }
}

/** Total rows in a week view (both lanes), for the header's `N 条`. */
export function weekTotal(view: WeekView): number {
  return view.columns.reduce(
    (sum, column) => sum + column.events.length + column.pending.length,
    0
  )
}
