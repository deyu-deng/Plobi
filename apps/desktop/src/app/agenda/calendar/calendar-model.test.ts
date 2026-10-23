import { describe, expect, it } from 'vitest'

import {
  addDays,
  addMonths,
  dayKeyOf,
  eventsByDay,
  isoBounds,
  monthGrid,
  parseDayKey,
  rangeFor,
  startOfWeek,
  stepAnchor,
  weekKeys,
  yearMonths
} from './calendar-model'

const WED = '2026-10-21'

describe('calendar-model date math', () => {
  it('round-trips a local day key without a UTC detour', () => {
    expect(dayKeyOf(parseDayKey(WED))).toBe(WED)
    expect(addDays(WED, 1)).toBe('2026-10-22')
    expect(addDays(WED, -21)).toBe('2026-09-30')
  })

  it('anchors month stepping on the 1st, so Jan 31 + 1 month is February', () => {
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-01')
    expect(addMonths('2026-01-31', -1)).toBe('2025-12-01')
    expect(stepAnchor('month', '2026-12-15', 1)).toBe('2027-01-01')
    expect(stepAnchor('year', '2026-10-21', 1)).toBe('2027-10-01')
  })

  it('starts weeks on Sunday and always returns seven consecutive days', () => {
    expect(startOfWeek(WED)).toBe('2026-10-18')
    expect(weekKeys(WED)).toEqual([
      '2026-10-18',
      '2026-10-19',
      '2026-10-20',
      '2026-10-21',
      '2026-10-22',
      '2026-10-23',
      '2026-10-24'
    ])
  })

  it('builds a 6×7 month grid that starts on Sunday and contains the whole month', () => {
    const grid = monthGrid('2026-10-01')

    expect(grid).toHaveLength(42)
    expect(grid[0]).toBe('2026-09-27')
    expect(grid[41]).toBe('2026-11-07')
    expect(grid).toContain('2026-10-01')
    expect(grid).toContain('2026-10-31')
  })

  it('handles leap years without inventing a 29th day elsewhere', () => {
    const february = monthGrid('2028-02-10')

    expect(february).toContain('2028-02-29')
    expect(monthGrid('2027-02-10')).not.toContain('2027-02-29')
  })

  it('lists twelve month anchors for the year view', () => {
    expect(yearMonths('2026-10-21')).toHaveLength(12)
    expect(yearMonths('2026-10-21')[0]).toBe('2026-01-01')
    expect(yearMonths('2026-10-21')[11]).toBe('2026-12-01')
  })

  it('fetches the whole week in day mode so the little week row can show counts', () => {
    expect(rangeFor('day', WED)).toEqual({ from: '2026-10-18', to: '2026-10-24' })
    expect(rangeFor('week', WED)).toEqual({ from: '2026-10-18', to: '2026-10-24' })
    expect(rangeFor('month', WED)).toEqual({ from: '2026-09-27', to: '2026-11-07' })
    expect(rangeFor('year', WED)).toEqual({ from: '2026-01-01', to: '2026-12-31' })
    expect(isoBounds(rangeFor('day', WED))).toEqual([
      '2026-10-18T00:00:00',
      '2026-10-24T23:59:59'
    ])
  })
})

describe('eventsByDay', () => {
  it('buckets by local day and sorts inside each bucket', () => {
    const events = [
      { id: 'b', start_at: '2026-10-21T15:00:00', title: 'b' },
      { id: 'a', start_at: '2026-10-21T09:00:00', title: 'a' },
      { id: 'c', start_at: '2026-10-22T08:00:00', title: 'c' }
    ] as never

    const buckets = eventsByDay(events)

    expect([...buckets.keys()]).toEqual(['2026-10-21', '2026-10-22'])
    expect(buckets.get('2026-10-21')?.map(event => event.id)).toEqual(['a', 'b'])
  })
})
