/**
 * Pure date math for the four calendar modes (year / month / week / day).
 *
 * Everything here works on **local calendar keys** (`YYYY-MM-DD`), never on
 * UTC instants: the agenda backend stores naive local ISO strings, and a
 * `toISOString()` round-trip is what made the rail ask for yesterday at
 * UTC+8 (裁定 33.5). The module is React-free so the grid math is unit-tested
 * without mounting anything.
 */

import type { AgendaEvent } from '@/types/plobi'

export type CalendarMode = 'day' | 'month' | 'week' | 'year'

export const CALENDAR_MODES: readonly CalendarMode[] = ['year', 'month', 'week', 'day']

/** Sunday-first week, matching the existing week row and 鸿蒙日历的默认. */
export const WEEK_STARTS_ON = 0

export interface DayRange {
  /** Inclusive local day key. */
  from: string
  /** Inclusive local day key. */
  to: string
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

export function dayKeyOf(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function parseDayKey(key: string): Date {
  const [year, month, day] = key.split('-').map(Number)

  return new Date(year, (month ?? 1) - 1, day ?? 1)
}

export function addDays(key: string, delta: number): string {
  const date = parseDayKey(key)

  date.setDate(date.getDate() + delta)

  return dayKeyOf(date)
}

export function addMonths(key: string, delta: number): string {
  const date = parseDayKey(key)

  // Anchor to the 1st: moving Jan 31 +1 month must land in February, not March.
  return dayKeyOf(new Date(date.getFullYear(), date.getMonth() + delta, 1))
}

export function addYears(key: string, delta: number): string {
  const date = parseDayKey(key)

  return dayKeyOf(new Date(date.getFullYear() + delta, date.getMonth(), 1))
}

export function startOfWeek(key: string): string {
  const date = parseDayKey(key)
  const offset = (date.getDay() - WEEK_STARTS_ON + 7) % 7

  return addDays(key, -offset)
}

export function weekKeys(key: string): string[] {
  const start = startOfWeek(key)

  return Array.from({ length: 7 }, (_, index) => addDays(start, index))
}

/** 6×7 grid covering the month, including the adjacent days that fill the rows. */
export function monthGrid(key: string): string[] {
  const date = parseDayKey(key)
  const first = dayKeyOf(new Date(date.getFullYear(), date.getMonth(), 1))
  const start = startOfWeek(first)

  return Array.from({ length: 42 }, (_, index) => addDays(start, index))
}

export function monthOf(key: string): number {
  return parseDayKey(key).getMonth()
}

export function yearOf(key: string): number {
  return parseDayKey(key).getFullYear()
}

export function isSameMonth(left: string, right: string): boolean {
  return left.slice(0, 7) === right.slice(0, 7)
}

/** 12 month keys (`YYYY-MM-01`) for the year view. */
export function yearMonths(key: string): string[] {
  const year = yearOf(key)

  return Array.from({ length: 12 }, (_, month) => `${year}-${pad(month + 1)}-01`)
}

export function rangeFor(mode: CalendarMode, anchor: string): DayRange {
  if (mode === 'year') {
    const year = yearOf(anchor)

    return { from: `${year}-01-01`, to: `${year}-12-31` }
  }

  if (mode === 'month') {
    const grid = monthGrid(anchor)

    return { from: grid[0], to: grid[grid.length - 1] }
  }

  if (mode === 'week') {
    const days = weekKeys(anchor)

    return { from: days[0], to: days[days.length - 1] }
  }

  // Day mode still fetches the whole week: the little week row needs the counts.
  const days = weekKeys(anchor)

  return { from: days[0], to: days[days.length - 1] }
}

export function stepAnchor(mode: CalendarMode, anchor: string, delta: number): string {
  if (mode === 'year') {
    return addYears(anchor, delta)
  }

  if (mode === 'month') {
    return addMonths(anchor, delta)
  }

  if (mode === 'week') {
    return addDays(anchor, delta * 7)
  }

  return addDays(anchor, delta)
}

/** `{ 'YYYY-MM-DD': events[] }` — one pass, stable by start time. */
export function eventsByDay(events: AgendaEvent[]): Map<string, AgendaEvent[]> {
  const buckets = new Map<string, AgendaEvent[]>()

  for (const event of events) {
    const key = (event.start_at ?? '').slice(0, 10)

    if (!key) {
      continue
    }

    const bucket = buckets.get(key)

    if (bucket) {
      bucket.push(event)
    } else {
      buckets.set(key, [event])
    }
  }

  for (const bucket of buckets.values()) {
    bucket.sort((left, right) => left.start_at.localeCompare(right.start_at))
  }

  return buckets
}

/** The two ISO bounds the backend understands (`YYYY-MM-DDT00:00:00`). */
export function isoBounds(range: DayRange): [string, string] {
  return [`${range.from}T00:00:00`, `${range.to}T23:59:59`]
}
