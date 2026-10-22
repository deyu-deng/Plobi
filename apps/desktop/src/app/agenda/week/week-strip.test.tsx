import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import type { AgendaEvent } from '@/types/plobi'

import { AgendaWeekStrip } from './week-strip'

/**
 * Render-level checks for the week row. These prove the *meaning* the user gets — the words
 * that matter (today / past / unknown conflicts) are still there, carried by `aria-label` and
 * `title`, while the visible layer is glyph-first (用户 10-22「少一点文字，多用矢量图标」).
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
    // Pinned by test id, not by weekday text: the day glyphs also contain 「一」.
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
    // A past day says so too — the word rides in the aria-label, the glyph in the cell.
    const monday = screen.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-05"]')

    expect(monday?.getAttribute('data-past')).toBe('true')
    expect(monday?.getAttribute('aria-label')).toContain('已过')
  })

  it('counts rows per column, keeps pending visible, and draws kinds as glyphs', () => {
    strip()
    const grid = screen.getByTestId('agenda-week-strip')
    const monday = grid.querySelector('[data-day="2026-10-05"]')
    const wednesday = grid.querySelector('[data-day="2026-10-07"]')

    expect(monday?.getAttribute('data-count')).toBe('1')
    expect(monday?.querySelector('.codicon-check')).not.toBeNull()
    expect(wednesday?.getAttribute('data-pending')).toBe('1')
    expect(wednesday?.querySelector('.codicon-bell-dot')).not.toBeNull()
    expect(grid.querySelector('[data-testid="week-total"]')?.getAttribute('title')).toBe('本周 3 条')
  })

  it('says conflicts are not loaded rather than inventing a zero', () => {
    const bare = strip()
    const columns = [...bare.getByTestId('agenda-week-strip').querySelectorAll('[data-testid="agenda-week-day"]')]

    expect(columns.every(column => column.getAttribute('data-conflict-state') === 'unknown')).toBe(true)
    expect(columns.some(column => column.getAttribute('data-conflict-state') === 'clean')).toBe(false)
    bare.unmount()

    const loaded = strip({ conflictsByDay: { '2026-10-10': [{ end_at: 'x', left_id: 'a', right_id: 'b', start_at: 'y' }] } })
    const column = loaded.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-10"]')

    expect(column?.getAttribute('data-conflict-state')).toBe('conflict')
    expect(column?.getAttribute('aria-label')).toContain('冲突 1')
  })

  it('pans whole weeks and returns to this week', () => {
    const view = strip()
    const before = view.getByTestId('agenda-week-strip').textContent

    fireEvent.click(view.getByTestId('week-next'))
    expect(view.getByTestId('agenda-week-strip').textContent).not.toBe(before)
    expect(view.getByTestId('agenda-week-strip').querySelector('[data-day="2026-10-12"]')).not.toBeNull()

    // Back-and-forth lands where it started, and the today button wins over any offset.
    fireEvent.click(view.getByTestId('week-prev'))
    fireEvent.click(view.getByTestId('week-next'))
    fireEvent.click(view.getByTestId('week-next'))
    fireEvent.click(view.getByTestId('week-today'))
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
      expect(grid.querySelector('[data-day="2026-10-10"]')?.getAttribute('data-conflict-state')).toBe('conflict')
    })
    // Answered-and-clean is clean; nothing was asked-for-nicely.
    expect(grid.querySelector('[data-day="2026-10-05"]')?.getAttribute('data-conflict-state')).toBe('clean')
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
      expect(grid.querySelector('[data-day="2026-10-10"]')?.getAttribute('data-conflict-state')).toBe('unknown')
    })
    // Seven unknowns, zero claims of cleanliness, and the rows still render.
    const columns = [...grid.querySelectorAll('[data-testid="agenda-week-day"]')]

    expect(columns.every(column => column.getAttribute('data-conflict-state') === 'unknown')).toBe(true)
    expect(grid.textContent).toContain('10-05')
  })
})
