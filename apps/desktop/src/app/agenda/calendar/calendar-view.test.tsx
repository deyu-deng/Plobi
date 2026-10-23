import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { en } from '@/i18n/en'
import type { AgendaEvent } from '@/types/plobi'

import type { CalendarMode } from './calendar-model'
import { AgendaCalendar } from './calendar-view'

const NOW = new Date('2026-10-21T10:30:00')
const ANCHOR = '2026-10-21'

function event(id: string, title: string, start: string, end: null | string = null): AgendaEvent {
  return {
    created_at: '2026-10-20T00:00:00Z',
    end_at: end,
    id,
    kind: 'task',
    source: 'manual',
    start_at: start,
    status: 'confirmed',
    title,
    updated_at: '2026-10-20T00:00:00Z'
  }
}

const EVENTS = [
  event('e1', '高数课', `${ANCHOR}T09:00:00`, `${ANCHOR}T10:00:00`),
  event('e2', '交作业', `${ANCHOR}T20:00:00`),
  event('e3', '组会', '2026-10-23T15:00:00', '2026-10-23T16:00:00')
]

function renderCalendar(mode: CalendarMode, overrides: Partial<Parameters<typeof AgendaCalendar>[0]> = {}) {
  const props = {
    anchor: ANCHOR,
    events: EVENTS,
    mode,
    now: NOW,
    onAnchorChange: vi.fn(),
    onCreate: vi.fn(),
    onImport: vi.fn(),
    onModeChange: vi.fn(),
    onSelectEvent: vi.fn(),
    selectedId: null,
    t: en.agenda,
    ...overrides
  }

  return { props, ...render(<AgendaCalendar {...props} />) }
}

afterEach(cleanup)

describe('AgendaCalendar modes', () => {
  it('day mode paints one time column and labels a missing end as a point', () => {
    renderCalendar('day')

    expect(screen.getByTestId(`calendar-column-${ANCHOR}`)).toBeTruthy()
    expect(screen.getByTestId('calendar-block-e1')).toBeTruthy()
    expect(screen.getByText('09:00–10:00')).toBeTruthy()
    // The deadline has no end: it is one title line at 20:00, not a fake hour.
    expect(screen.getByText('交作业')).toBeTruthy()
    expect(screen.queryByTestId('calendar-column-2026-10-22')).toBeNull()
  })

  it('week mode paints seven columns and still keeps the real clocks', () => {
    renderCalendar('week')

    expect(screen.getAllByTestId(/^calendar-column-/)).toHaveLength(7)
    expect(screen.getByTestId('calendar-column-2026-10-23')).toBeTruthy()
    expect(screen.getByText('组会')).toBeTruthy()
  })

  it('month mode shows a 6×7 grid and clicking a day switches to day view', () => {
    const onAnchorChange = vi.fn()
    const onModeChange = vi.fn()

    renderCalendar('month', { onAnchorChange, onModeChange })

    expect(screen.getAllByTestId(/^month-day-/)).toHaveLength(42)
    expect(screen.getByText('高数课')).toBeTruthy()

    fireEvent.click(screen.getByTestId('month-day-2026-10-23'))

    expect(onAnchorChange).toHaveBeenCalledWith('2026-10-23')
    expect(onModeChange).toHaveBeenCalledWith('day')
  })

  it('clicking an event chip selects it without opening the day', () => {
    const onSelectEvent = vi.fn()
    const onModeChange = vi.fn()

    renderCalendar('month', { onModeChange, onSelectEvent })

    fireEvent.click(screen.getByText('高数课'))

    expect(onSelectEvent).toHaveBeenCalledWith('e1')
    expect(onModeChange).not.toHaveBeenCalled()
  })

  it('year mode shows twelve mini months and a day opens the day view', () => {
    const onAnchorChange = vi.fn()
    const onModeChange = vi.fn()

    renderCalendar('year', { onAnchorChange, onModeChange })

    expect(screen.getAllByTestId(/^year-month-/)).toHaveLength(12)
    fireEvent.click(screen.getAllByLabelText(ANCHOR)[0])

    expect(onAnchorChange).toHaveBeenCalledWith(ANCHOR)
    expect(onModeChange).toHaveBeenCalledWith('day')
  })

  it('mode buttons ask the board to switch without moving the anchor', () => {
    const onModeChange = vi.fn()
    const onAnchorChange = vi.fn()

    renderCalendar('day', { onAnchorChange, onModeChange })

    fireEvent.click(screen.getByRole('button', { name: 'Month' }))

    expect(onModeChange).toHaveBeenCalledWith('month')
    expect(onAnchorChange).not.toHaveBeenCalled()
  })
})
