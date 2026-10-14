import { describe, expect, it } from 'vitest'

import type { AgendaConflict, AgendaEvent } from '@/types/plobi'

import {
  addDays,
  buildWeekView,
  dayKeyFromDate,
  dayKeyToDate,
  DAYS_PER_WEEK,
  eventDayKey,
  mondayKeyOf,
  shiftWeek,
  weekDayKeys,
  weekTotal
} from './week-model'

/**
 * WP-AGENDA-VIEW slice 1. Expected values below come from the calendar itself (weekday
 * arithmetic written out by hand), not from calling the implementation twice — otherwise a
 * wrong anchor would just be asserted twice and agree with itself. The same anchors are pinned
 * on the phone side (`Plobi-Harmony-App/entry/src/test/WeekView.test.ets`), so the two surfaces
 * fail together if either drifts.
 */

function event(overrides: Partial<AgendaEvent> & { id: string; start_at: string }): AgendaEvent {
  return {
    created_at: overrides.start_at,
    end_at: overrides.end_at ?? null,
    id: overrides.id,
    kind: overrides.kind ?? 'task',
    source: overrides.source ?? 'manual',
    start_at: overrides.start_at,
    status: overrides.status ?? 'confirmed',
    title: overrides.title ?? overrides.id,
    updated_at: overrides.start_at
  }
}

describe('agenda week model', () => {
  it('anchors a week on Monday, including across month and year edges', () => {
    // 2026-10-10 is a Saturday ⇒ the week starts 2026-10-05.
    expect(mondayKeyOf('2026-10-10')).toBe('2026-10-05')
    expect(mondayKeyOf('2026-10-05')).toBe('2026-10-05')
    // Sunday is the *last* day of the Monday week, not the first of the next one.
    expect(mondayKeyOf('2026-10-11')).toBe('2026-10-05')
    // 2027-01-01 is a Friday ⇒ still the week opened 2026-12-28 (crosses the year back).
    expect(mondayKeyOf('2027-01-01')).toBe('2026-12-28')
    // 2028-03-01 is a Wednesday in a leap year ⇒ Monday was 2028-02-28.
    expect(mondayKeyOf('2028-03-01')).toBe('2028-02-28')
    expect(mondayKeyOf('not-a-day')).toBe('')
  })

  it('lays seven ascending local days under one Monday', () => {
    const keys = weekDayKeys('2026-10-05')

    expect(keys).toHaveLength(DAYS_PER_WEEK)
    expect(keys[0]).toBe('2026-10-05')
    expect(keys[DAYS_PER_WEEK - 1]).toBe('2026-10-11')
    expect([...keys].sort()).toEqual(keys)
    // Crossing the month inside one week.
    expect(weekDayKeys('2026-10-26')[6]).toBe('2026-11-01')
    expect(weekDayKeys('2026-12-28')[6]).toBe('2027-01-03')
    expect(weekDayKeys('nope')).toEqual([])
  })

  it('steps whole weeks and single days without guessing bad input', () => {
    expect(shiftWeek('2026-10-05', 1)).toBe('2026-10-12')
    expect(shiftWeek('2026-10-05', -1)).toBe('2026-09-28')
    expect(shiftWeek('2026-12-28', 1)).toBe('2027-01-04')
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-02-28', 1)).toBe('2026-03-01')
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDays('bogus', 3)).toBe('')
  })

  it('refuses impossible calendar days instead of rolling them over', () => {
    expect(dayKeyToDate('2026-02-30')).toBeNull()
    expect(dayKeyToDate('2026-13-01')).toBeNull()
    expect(dayKeyToDate('2026-10-14')).not.toBeNull()
  })

  it('reads the local day off a naive ISO stamp and never invents one', () => {
    expect(eventDayKey('2026-10-14T23:59:59')).toBe('2026-10-14')
    expect(eventDayKey('2026-10-14T00:00:00')).toBe('2026-10-14')
    expect(eventDayKey('')).toBe('')
    expect(eventDayKey(undefined)).toBe('')
    expect(eventDayKey(null)).toBe('')
    expect(eventDayKey('later today maybe')).toBe('')
    // A day key formatted from a Date round-trips.
    expect(dayKeyFromDate(new Date(2026, 9, 14))).toBe('2026-10-14')
    expect(dayKeyFromDate(new Date(2026, 0, 1))).toBe('2026-01-01')
  })

  it('buckets a week, splits the pending lane, and counts what it drops', () => {
    const events: AgendaEvent[] = [
      event({ id: 'fri', start_at: '2026-10-09T09:00:00' }),
      event({ id: 'mon-late', start_at: '2026-10-05T18:00:00' }),
      event({ id: 'mon-early', start_at: '2026-10-05T08:00:00' }),
      event({ id: 'wait', start_at: '2026-10-07T12:00:00', status: 'pending' }),
      // Outside the visible week ⇒ counted, not silently hidden.
      event({ id: 'next-week', start_at: '2026-10-13T09:00:00' }),
      // No parsable day ⇒ its own counter, never a fake column.
      event({ id: 'junk', start_at: '' })
    ]

    const view = buildWeekView({
      events,
      mondayKey: '2026-10-05',
      todayKey: '2026-10-10'
    })

    expect(view.columns.map(column => column.dayKey)).toEqual(weekDayKeys('2026-10-05'))
    expect(view.droppedCount).toBe(1)
    expect(view.invalidCount).toBe(1)
    expect(weekTotal(view)).toBe(4)

    const monday = view.columns[0]

    expect(monday.events.map(row => row.id)).toEqual(['mon-early', 'mon-late'])
    expect(monday.dayIndex).toBe(0)
    expect(monday.isPast).toBe(true)
    expect(monday.isToday).toBe(false)

    const wednesday = view.columns[2]

    expect(wednesday.pending.map(row => row.id)).toEqual(['wait'])
    expect(wednesday.events).toHaveLength(0)

    const saturday = view.columns[5]

    expect(saturday.dayKey).toBe('2026-10-10')
    expect(saturday.isToday).toBe(true)
    expect(saturday.isPast).toBe(false)
  })

  it('carries backend conflicts and never derives them from the rows', () => {
    // Two rows that *would* overlap if anyone were tempted to compute it here.
    const events: AgendaEvent[] = [
      event({ end_at: '2026-10-10T11:00:00', id: 'a', start_at: '2026-10-10T10:00:00' }),
      event({ end_at: '2026-10-10T10:30:00', id: 'b', start_at: '2026-10-10T10:15:00' })
    ]

    const conflicts: AgendaConflict[] = [
      { end_at: '2026-10-10T10:30:00', left_id: 'a', right_id: 'b', start_at: '2026-10-10T10:15:00' }
    ]

    const withoutSnapshot = buildWeekView({ events, mondayKey: '2026-10-05', todayKey: '2026-10-10' })
    const saturday = withoutSnapshot.columns[5]

    // The heart of 裁定 139④ / handset-app §3.1: no day snapshot means "unknown", **not** zero.
    expect(saturday.conflictKnown).toBe(false)
    expect(saturday.conflictCount).toBe(0)

    const withSnapshot = buildWeekView({
      conflictsByDay: { '2026-10-10': conflicts },
      events,
      mondayKey: '2026-10-05',
      todayKey: '2026-10-10'
    })

    expect(withSnapshot.columns[5].conflictKnown).toBe(true)
    expect(withSnapshot.columns[5].conflictCount).toBe(1)
    // A day the backend says is clean stays clean even though rows could be misread.
    expect(
      buildWeekView({ conflictsByDay: { '2026-10-10': [] }, events, mondayKey: '2026-10-05', todayKey: '2026-10-10' })
        .columns[5]
        .conflictCount
    ).toBe(0)
    // Rows are passed through untouched — `overlaps` is the backend's field, not ours to fill.
    expect(withSnapshot.columns[5].events[0].id).toBe('a')
    expect(withSnapshot.columns[5].events[0].overlaps).toBeUndefined()
  })
})
