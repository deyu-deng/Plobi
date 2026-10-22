import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import type { AgendaEvent } from '@/types/plobi'

import { AgendaWeekStrip } from './week-strip'

/**
 * Render-level checks for the week row. These prove the *text* the user gets — colour is
 * never the only carrier, and a day with no backend snapshot must not claim "no conflicts".
 * Whether the row "looks like a calendar" is deliberately **not** asserted here: that is the
 * user's call (架构师 10-14 ②), not something jsdom can decide.
 */

const zh = TRANSLATIONS.zh

function row(overrides: Partial<AgendaEvent> & { id: string; start_at: string }): AgendaEvent {
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

function strip(props: Partial<Parameters<typeof AgendaWeekStrip>[0]> = {}) {
  const base = {
    events: [
      row({ id: 'mon', start_at: '2026-10-05T09:00:00' }),
      row({ id: 'wed-pending', start_at: '2026-10-07T14:00:00', status: 'pending' }),
      row({ id: 'sat', start_at: '2026-10-10T10:00:00' })
    ],
    onPickDay: () => undefined,
    pickedDayKey: null as null | string,
    todayKey: '2026-10-10',
    translations: zh,
    ...props
  }

  return { ...base, ...render(<AgendaWeekStrip {...base} />) }
}

afterEach(() => {
  cleanup()
})

describe('AgendaWeekStrip', () => {
  it('shows seven columns for the Monday-anchored week containing today', () => {
    const { getByTestId, getAllByTestId } = strip()
    const grid = getByTestId('agenda-week-strip')
    // Pinned by test id, not by weekday text: 「下一周」 also contains 「一」.
    const days = getAllByTestId('agenda-week-day')

    expect(days).toHaveLength(7)
    // Week of Mon 2026-10-05 … Sun 2026-10-11, rendered as MM-DD.
    expect(days[0]?.textContent).toContain('10-05')
    expect(days[6]?.textContent).toContain('10-11')
    expect(within(grid).getAllByTestId('agenda-week-day')).toHaveLength(7)
  })

  it('marks today with a word, not only with styling', () => {
    strip()
    const today = screen.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-10"]')

    expect(today?.textContent).toContain('今天')
    expect(today?.getAttribute('aria-current')).toBe('date')
    // A past day says so in words too.
    const monday = screen.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-05"]')

    expect(monday?.textContent).toContain('已过')
  })

  it('counts rows per column and keeps the pending lane visible', () => {
    strip()
    const grid = screen.getByTestId('agenda-week-strip')
    const monday = grid.querySelector('[data-day="2026-10-05"]')
    const wednesday = grid.querySelector('[data-day="2026-10-07"]')

    expect(monday?.textContent).toContain('1 条')
    expect(wednesday?.textContent).toContain('待确认 1')
    expect(grid.textContent).toContain('本周 3 条')
  })

  it('says conflicts are not loaded rather than inventing a zero', () => {
    const bare = strip()

    expect(bare.getByTestId('agenda-week-strip').textContent).toContain('冲突未取')
    expect(bare.getByTestId('agenda-week-strip').textContent).not.toContain('无冲突')
    bare.unmount()

    const loaded = strip({ conflictsByDay: { '2026-10-10': [{ end_at: 'x', left_id: 'a', right_id: 'b', start_at: 'y' }] } })
    const column = loaded.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-10"]')

    expect(column?.textContent).toContain('冲突 1')
  })

  it('pans whole weeks and returns to this week', () => {
    const view = strip()
    const before = view.getByTestId('agenda-week-strip').textContent

    fireEvent.click(view.getByText('下一周'))
    expect(view.getByTestId('agenda-week-strip').textContent).not.toBe(before)
    expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-12"]')).not.toBeNull()

    // Back-and-forth lands where it started, and "回到本周" wins over any offset.
    fireEvent.click(view.getByText('上一周'))
    fireEvent.click(view.getByText('下一周'))
    fireEvent.click(view.getByText('下一周'))
    fireEvent.click(view.getByText('回到本周'))
    expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-05"]')).not.toBeNull()
    expect(view.getByTestId('agenda-week-strip').textContent).toBe(before)
  })

  it('hands the picked day up and clears on a second click', () => {
    const calls: Array<null | string> = []
    const onPickDay = (dayKey: null | string) => calls.push(dayKey)

    const first = render(
      <AgendaWeekStrip
        events={[row({ id: 'sat', start_at: '2026-10-10T10:00:00' })]}
        onPickDay={onPickDay}
        pickedDayKey={null}
        todayKey="2026-10-10"
        translations={zh}
      />
    )

    fireEvent.click(within(first.getByTestId('agenda-week-strip')).getByRole('button', { name: /10-05/ }))
    first.unmount()

    render(
      <AgendaWeekStrip
        events={[]}
        onPickDay={onPickDay}
        pickedDayKey="2026-10-05"
        todayKey="2026-10-10"
        translations={zh}
      />
    )
    fireEvent.click(within(screen.getByTestId('agenda-week-strip')).getByRole('button', { name: /10-05/ }))

    expect(calls).toEqual(['2026-10-05', null])
    // aria-pressed is the machine-readable half of "this column is the active filter".
    expect(
      within(screen.getByTestId('agenda-week-strip'))
        .getByRole('button', { name: /10-05/ })
        .getAttribute('aria-pressed')
    ).toBe('true')
  })

  it('renders nothing when there is no parsable today to anchor on', () => {
    render(
      <AgendaWeekStrip events={[]} onPickDay={() => undefined} pickedDayKey={null} todayKey="" translations={zh} />
    )

    expect(screen.queryByTestId('agenda-week-strip')).toBeNull()
  })

  // --- slice 2: the seven day snapshots behind the conflict column -------------------------

  it('fills the conflict layer as days answer, and never invents a zero for the rest (C1)', async () => {
    const events = [row({ id: 'sat', start_at: '2026-10-10T10:00:00' })]

    const view = render(
      <AgendaWeekStrip
        events={events}
        fetchDay={async (dayKey: string) =>
          dayKey === '2026-10-10'
            ? { conflicts: [{ end_at: 'x', left_id: 'a', right_id: 'b', start_at: 'y' }], date: dayKey, events: [], plan_items: [] }
            : { conflicts: [], date: dayKey, events: [], plan_items: [] }
        }
        onPickDay={() => undefined}
        pickedDayKey={null}
        todayKey="2026-10-10"
        translations={zh}
      />
    )

    const grid = view.getByTestId('agenda-week-strip')

    await waitFor(() => {
      expect(grid.querySelector('[data-day="2026-10-10"]')?.textContent).toContain('冲突 1')
    })
    // Answered-and-clean reads as 无冲突; nothing was asked-for-nicely.
    expect(grid.querySelector('[data-day="2026-10-05"]')?.textContent).toContain('无冲突')
  })

  it('keeps every column honest when the whole week fails to answer (C2)', async () => {
    const view = render(
      <AgendaWeekStrip
        events={[row({ id: 'mon', start_at: '2026-10-05T09:00:00' })]}
        fetchDay={async () => {
          throw new Error('backend down')
        }}
        onPickDay={() => undefined}
        pickedDayKey={null}
        todayKey="2026-10-10"
        translations={zh}
      />
    )

    const grid = view.getByTestId('agenda-week-strip')

    await waitFor(() => {
      expect(grid.textContent).toContain('冲突未取')
    })
    // Seven unknowns, zero claims of cleanliness, and the rows still render.
    expect(grid.textContent).not.toContain('无冲突')
    expect(grid.textContent).toContain('10-05')
  })

  it('hands the answered plans back up so the page can show 明天 without a second round of calls', async () => {
    const seen: Array<{ loadedCount: number; planKeys: string[] }> = []

    const view = render(
      <AgendaWeekStrip
        events={[]}
        fetchDay={async (dayKey: string) => ({
          conflicts: [],
          date: dayKey,
          events: [],
          plan_items: dayKey === '2026-10-11' ? [{ end_at: null, id: 'p', lane: 'plan' as const, start_at: '2026-10-11T10:00:00', title: '推进块' }] : [],
          plan_status: dayKey === '2026-10-11' ? 'pending' : null
        })}
        onPickDay={() => undefined}
        onSummaries={state => seen.push({ loadedCount: state.loadedCount, planKeys: Object.keys(state.plansByDay) })}
        pickedDayKey={null}
        todayKey="2026-10-10"
        translations={zh}
      />
    )

    await waitFor(() => expect(seen).toHaveLength(1))
    expect(seen[0]?.loadedCount).toBe(7)
    // Sunday of the visible week is the only day with a plan row.
    expect(seen[0]?.planKeys).toEqual(['2026-10-11'])
    expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-11"]')?.textContent).toContain('无冲突')
  })

  it('ignores a late answer from a week the user has panned away from', async () => {
    type Resolve = (value: unknown) => void

    const pending: Array<{ dayKey: string; resolve: Resolve }> = []

    const fetchDay = (dayKey: string) =>
      new Promise(resolve => {
        pending.push({ dayKey, resolve })
      })

    const view = render(
      <AgendaWeekStrip
        events={[]}
        fetchDay={fetchDay as never}
        onPickDay={() => undefined}
        pickedDayKey={null}
        todayKey="2026-10-10"
        translations={zh}
      />
    )

    // First week: seven requests in flight.
    await waitFor(() => expect(pending).toHaveLength(7))
    const staleWeek = [...pending]

    pending.length = 0
    fireEvent.click(view.getByText('下一周'))
    await waitFor(() => expect(pending).toHaveLength(7))

    // Answer the new week, then let the old week dribble in late.
    for (const call of pending) {
      call.resolve({ conflicts: [], date: call.dayKey, events: [], plan_items: [] })
    }

    await waitFor(() => {
      expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-12"]')?.textContent).toContain('无冲突')
    })

    for (const call of staleWeek) {
      call.resolve({
        conflicts: [{ end_at: 'x', left_id: 'a', right_id: 'b', start_at: 'y' }],
        date: call.dayKey,
        events: [],
        plan_items: []
      })
    }

    // The late week-A answer must not wipe week-B's loaded state.
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-12"]')?.textContent).toContain('无冲突')
  })
})
