import { describe, expect, it, vi } from 'vitest'

import type { AgendaConflict, AgendaDay } from '@/types/plobi'

import {
  createDayFetcher,
  type DayFetchResult,
  fetchWeekConflicts,
  reduceWeekConflicts
} from './week-snapshots'

/**
 * Slice 2: the seven `/api/agenda/day` snapshots behind the week row's conflict column.
 *
 * The user already signed this shape for the phone side — **D1** (loop seven `fetchDay`s),
 * **C1** progressive fill, **C2** one dead day degrades only its own column
 * (`Docs/tasks/app-calendar-week-task.md`). These tests pin the same three behaviours here so the
 * two surfaces stay one design (裁定 139④(b)), plus the one thing neither surface may ever do:
 * turn "we could not ask" into "the day is clean".
 */

const MONDAY_KEYS = ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11']

function conflict(left: string, right: string): AgendaConflict {
  return { end_at: '2026-10-10T10:30:00', left_id: left, right_id: right, start_at: '2026-10-10T10:15:00' }
}

function day(overrides: Partial<AgendaDay> = {}): AgendaDay {
  return {
    anchors: [],
    date: '2026-10-10',
    events: [],
    plan_items: [],
    ...overrides
  }
}

describe('reduceWeekConflicts', () => {
  it('keeps answered days and drops what never answered', () => {
    const results: DayFetchResult[] = [
      { conflicts: [conflict('a', 'b')], dayKey: '2026-10-10', ok: true, plan: undefined },
      { dayKey: '2026-10-11', error: new Error('boom'), ok: false },
      { dayKey: '2026-10-09', error: new Error('shape'), ok: false }
    ]

    const state = reduceWeekConflicts(results)

    expect(state.loadedCount).toBe(1)
    expect(Object.keys(state.conflictsByDay)).toEqual(['2026-10-10'])
    expect(state.conflictsByDay['2026-10-11']).toBeUndefined()
    expect(state.plansByDay['2026-10-10']).toBeUndefined()
  })

  it('treats an empty conflict list as an answered, clean day', () => {
    const state = reduceWeekConflicts([
      { conflicts: [], dayKey: '2026-10-05', ok: true, plan: { items: [], status: 'pending' } }
    ])

    expect(state.loadedCount).toBe(1)
    expect(state.conflictsByDay['2026-10-05']).toEqual([])
    // 裁定 140: the plan rides in on the same answer, keyed the same way — no second round of calls.
    expect(state.plansByDay['2026-10-05']).toEqual({ items: [], status: 'pending' })
  })
})

describe('fetchWeekConflicts', () => {
  it('asks once per visible day (D1: seven snapshots, not one week query)', async () => {
    const fetchDay = vi.fn(async (dayKey: string) => day({ conflicts: [], date: dayKey }))
    const state = await fetchWeekConflicts(MONDAY_KEYS, fetchDay)

    expect(fetchDay).toHaveBeenCalledTimes(7)
    expect(fetchDay.mock.calls.map(call => call[0])).toEqual(MONDAY_KEYS)
    expect(state.loadedCount).toBe(7)
  })

  it('isolates a dead day and keeps the rest (C2 单列降级)', async () => {
    const state = await fetchWeekConflicts(MONDAY_KEYS, async (dayKey: string) => {
      if (dayKey === '2026-10-07') {
        throw new Error('backend unreachable')
      }

      if (dayKey === '2026-10-08') {
        // Answers, but with no conflict layer (older backend shape) ⇒ still "unknown".
        return day({ date: dayKey })
      }

      return day({ conflicts: dayKey === '2026-10-10' ? [conflict('a', 'b')] : [], date: dayKey })
    })

    expect(state.loadedCount).toBe(5)
    expect(state.conflictsByDay['2026-10-10']).toHaveLength(1)
    expect(state.conflictsByDay['2026-10-06']).toEqual([])
    expect(state.conflictsByDay['2026-10-07']).toBeUndefined()
    expect(state.conflictsByDay['2026-10-08']).toBeUndefined()
  })

  it('never turns a missing payload into a zero', async () => {
    const state = await fetchWeekConflicts(['2026-10-05'], async () => null)

    expect(state.loadedCount).toBe(0)
    expect(state.conflictsByDay).toEqual({})
    expect(state.plansByDay).toEqual({})
  })

  it('carries the evening plan for the days that have one, and silence for the days that do not', async () => {
    const items = [
      { end_at: '2026-10-15T11:00:00', id: 'pl-1', lane: 'plan' as const, start_at: '2026-10-15T10:00:00', title: '推进块' }
    ]

    const state = await fetchWeekConflicts(['2026-10-14', '2026-10-15'], async (dayKey: string) => {
      if (dayKey === '2026-10-15') {
        // What the 20:00 job leaves behind for the next day (裁定 140: the desktop reads this).
        return day({ conflicts: [], date: dayKey, plan_items: items, plan_status: 'pending' })
      }

      return day({ conflicts: [], date: dayKey })
    })

    expect(state.plansByDay['2026-10-15']).toEqual({ items, status: 'pending' })
    // No plan row ⇒ no key at all, so the section stays away instead of printing an empty header.
    expect(state.plansByDay['2026-10-14']).toBeUndefined()
    // Both days still answered for the conflict layer.
    expect(state.loadedCount).toBe(2)
  })
})

describe('createDayFetcher', () => {
  it('GETs the existing day endpoint and degrades to null when the bridge rejects', async () => {
    const api = vi.fn().mockResolvedValue(day({ conflicts: [] }))
    const previous = (window as { plobiDesktop?: unknown }).plobiDesktop

    Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: { api } })

    try {
      const payload = await createDayFetcher()('2026-10-10')

      expect(payload?.date).toBe('2026-10-10')
      // Same endpoint the console rail and the phone already use — this slice adds no REST surface.
      expect(api.mock.calls[0][0]).toMatchObject({
        method: 'GET',
        path: '/api/agenda/day?date=2026-10-10'
      })

      api.mockRejectedValue(new Error('down'))
      expect(await createDayFetcher()('2026-10-11')).toBeNull()
    } finally {
      if (previous) {
        Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: previous })
      } else {
        Reflect.deleteProperty(window, 'plobiDesktop')
      }
    }
  })
})
