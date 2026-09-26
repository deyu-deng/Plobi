import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import { Timeline } from './timeline'
import { shouldRefreshAgendaOnBusyChange } from './use-agenda-timeline'

function event(id: string, startAt: string, overrides: Partial<AgendaEvent> = {}): AgendaEvent {
  const now = '2026-08-30T00:00:00Z'

  return {
    confirm_seq: null,
    created_at: now,
    end_at: null,
    id,
    kind: 'task',
    source: 'manual',
    start_at: startAt,
    status: 'confirmed',
    title: `event-${id}`,
    updated_at: now,
    ...overrides
  }
}

function dateAt(offsetDays: number, hour: number): string {
  return dateAtTime(offsetDays, `${String(hour).padStart(2, '0')}:00`)
}

function dateAtTime(offsetDays: number, clock: string): string {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)

  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${clock}:00`
}

function styleOf(testId: string): string {
  return screen.getByTestId(testId).getAttribute('style') ?? ''
}

function topPxOf(testId: string): number {
  return Number((styleOf(testId).match(/top:\s*(-?\d+(?:\.\d+)?)px/) ?? [, 'NaN'])[1])
}

function leftPctOf(testId: string): number {
  return Number((styleOf(testId).match(/left:\s*calc\((\d+(?:\.\d+)?)%/) ?? [, 'NaN'])[1])
}

describe('Timeline (day axis)', () => {
  afterEach(() => {
    cleanup()
  })

  it('shows the empty hint only when there is no day to paint at all', () => {
    // Today + tomorrow always render (裁定 37.2 / 39), so the empty hint
    // is reserved for truly empty inputs (no events AND no avoid windows).
    vi.useFakeTimers()

    try {
      vi.setSystemTime(new Date(2026, 8, 16, 9, 0, 0))
      render(<Timeline events={[]} />)
      // Empty store ⇒ today's minimum axis still paints.
      expect(screen.queryByTestId('timeline-empty')).toBeNull()
      expect(screen.getByTestId(`timeline-day-${new Date().getFullYear()}-09-16`)).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('groups today and tomorrow under their section labels', () => {
    render(<Timeline events={[event('a', dateAt(0, 9)), event('b', dateAt(1, 14))]} />)

    expect(screen.getByText('Today')).toBeTruthy()
    expect(screen.getByText('Tomorrow')).toBeTruthy()
    expect(screen.getByText('event-a')).toBeTruthy()
    expect(screen.getByText('event-b')).toBeTruthy()
  })

  it('draws the 08:00–23:00 hour axis on each day', () => {
    render(<Timeline events={[event('a', dateAt(0, 9), { end_at: dateAt(0, 11) })]} />)

    // Today + tomorrow both render an axis, so tick labels repeat per day.
    expect(screen.getAllByText('08:00').length).toBeGreaterThan(0)
    expect(screen.getAllByText('23:00').length).toBeGreaterThan(0)
  })

  it('prints the start–end span on the block', () => {
    render(<Timeline events={[event('s', dateAt(0, 9), { end_at: dateAt(0, 11) })]} />)

    expect(screen.getByText('09:00–11:00')).toBeTruthy()
  })

  it('says the end is unwritten instead of inventing an hour', () => {
    render(<Timeline events={[event('o', dateAt(0, 9))]} />)

    expect(screen.getByText('no end time')).toBeTruthy()
  })

  it('keeps the pending tag on pending entries', () => {
    render(<Timeline events={[event('p', dateAt(0, 10), { status: 'pending' })]} />)

    expect(screen.getByText('pending')).toBeTruthy()
    expect(screen.getByText('event-p')).toBeTruthy()
  })

  it('renders side-by-side overlaps as different columns with a warning border', () => {
    render(
      <Timeline
        events={[
          event('left', dateAt(0, 14), { end_at: dateAt(0, 16) }),
          event('right', dateAt(0, 15), { end_at: dateAt(0, 17) })
        ]}
      />
    )

    const blocks = screen.getAllByTestId(/^timeline-block-/)
    const leftStyle = blocks.find(node => node.getAttribute('data-testid') === 'timeline-block-left')?.getAttribute('style') ?? ''
    const rightStyle = blocks.find(node => node.getAttribute('data-testid') === 'timeline-block-right')?.getAttribute('style') ?? ''

    // 裁定 31.1: each block must take only half of the axis content area so
    // the two collisions never overlap each other (the previous 10px nudge
    // hid the second block under the first).
    expect(leftStyle).toMatch(/width:\s*calc\(41%/)
    expect(rightStyle).toMatch(/width:\s*calc\(41%/)
    const leftLeft = Number((leftStyle.match(/left:\s*calc\((\d+(?:\.\d+)?)%/) ?? [, '0'])[1])
    const rightLeft = Number((rightStyle.match(/left:\s*calc\((\d+(?:\.\d+)?)%/) ?? [, '0'])[1])

    expect(rightLeft).toBeGreaterThan(leftLeft)
    expect(screen.getAllByTestId('timeline-block-left')[0].getAttribute('data-overlap')).toBe('true')
    expect(screen.getAllByTestId('timeline-block-right')[0].getAttribute('data-overlap')).toBe('true')
  })

  it('paints routine anchors as a distinct lane that never competes with event columns', () => {
    render(
      <Timeline
        anchors={[{ end_at: dateAt(0, 13), label: '午饭', start_at: dateAt(0, 12) }]}
        events={[event('lecture', dateAt(0, 9), { end_at: dateAt(0, 11) })]}
      />
    )

    const anchor = screen.getByText('午饭')

    expect(anchor).toBeTruthy()
    expect(anchor.closest('[data-lane]')?.getAttribute('data-lane')).toBe('anchor')
  })

  it('places a block on its own clock tick, not at the axis top (裁定 32.1)', () => {
    render(<Timeline events={[event('evening', dateAtTime(0, '18:30'), { end_at: dateAtTime(0, '22:30') })]} />)

    // 18:30 is 10.5h past the 08:00 axis start at 44px/h.
    const top = topPxOf('timeline-block-evening')

    expect(top).toBe(462)
    expect(top).toBeGreaterThan(0)
    expect(styleOf('timeline-block-evening')).toMatch(/top:\s*462px/)
  })

  it('keeps a morning block above an evening block instead of stacking both at the top', () => {
    render(
      <Timeline
        events={[
          event('morning', dateAtTime(0, '09:00'), { end_at: dateAtTime(0, '10:00') }),
          event('evening', dateAtTime(0, '18:30'), { end_at: dateAtTime(0, '22:30') })
        ]}
      />
    )

    expect(topPxOf('timeline-block-morning')).toBe(44)
    expect(topPxOf('timeline-block-evening')).toBe(462)
  })

  it('gives non-overlapping routines one full-width column, not two half-width slivers', () => {
    render(
      <Timeline
        anchors={[
          { end_at: dateAt(0, 13), label: '午饭', start_at: dateAt(0, 12) },
          { end_at: dateAt(0, 19), label: '晚饭', start_at: dateAt(0, 18) }
        ]}
        events={[]}
      />
    )

    const blocks = screen.getAllByTestId(/^timeline-block-anchor-/)

    expect(blocks).toHaveLength(2)
    // Same column → same left edge, and each takes the whole 82% content area.
    expect(leftPctOf('timeline-block-anchor-0')).toBe(leftPctOf('timeline-block-anchor-1'))
    for (const node of blocks) {
      expect(node.getAttribute('style')).toMatch(/width:\s*calc\(82%/)
    }
    expect(blocks.every(node => node.getAttribute('data-overlap') === null)).toBe(true)
  })

  it('marks only the colliding events as overlapping', () => {
    render(
      <Timeline
        events={[
          event('morning', dateAt(0, 9), { end_at: dateAt(0, 10) }),
          event('left', dateAt(0, 14), { end_at: dateAt(0, 16) }),
          event('right', dateAt(0, 15), { end_at: dateAt(0, 17) })
        ]}
      />
    )

    expect(screen.getByTestId('timeline-block-morning').getAttribute('data-overlap')).toBeNull()
    expect(screen.getByTestId('timeline-block-left').getAttribute('data-overlap')).toBe('true')
    expect(screen.getByTestId('timeline-block-right').getAttribute('data-overlap')).toBe('true')
  })

  it('splits a colliding class and breakfast into two columns on the shared grid (裁定 36.2)', () => {
    render(
      <Timeline
        anchors={[{ end_at: dateAtTime(0, '08:20'), label: '早餐', start_at: dateAtTime(0, '07:40') }]}
        events={[event('lecture', dateAtTime(0, '08:00'), { end_at: dateAtTime(0, '09:35') })]}
      />
    )

    // Breakfast starts first → column 0; the class moves beside it. Two
    // different column bands at half the content width each — never the same
    // left edge with a full-width translucent overlay.
    expect(leftPctOf('timeline-block-anchor-1')).toBeLessThan(leftPctOf('timeline-block-lecture'))
    expect(styleOf('timeline-block-lecture')).toMatch(/width:\s*calc\(41%/)
    expect(styleOf('timeline-block-anchor-1')).toMatch(/width:\s*calc\(41%/)
    // Each keeps its own clock row (07:40 breakfast above the 08:00 class).
    expect(topPxOf('timeline-block-anchor-1')).toBeLessThan(topPxOf('timeline-block-lecture'))
  })

  it('draws the "now" line on today only, exactly on the current clock mark (裁定 36.2)', () => {
    vi.useFakeTimers()

    try {
      vi.setSystemTime(new Date(2026, 8, 16, 10, 0, 0))

      const { unmount } = render(
        <Timeline events={[event('a', dateAt(0, 9), { end_at: dateAt(0, 11) })]} />
      )

      // 10:00 sits 2h past the 08:00 axis start at 44px/h.
      const nowEl = screen.getByTestId('timeline-now')
      expect(topPxOf('timeline-now')).toBe(88)
      // 裁定 40: the line uses --ui-accent, NOT the secondary stroke colour,
      // otherwise it disappears against the hour grid (regression). The
      // accent lives on the inner bar (a child span); the wrapper itself
      // only carries the layout flex / pointer-events classes.
      const innerBar = nowEl.querySelector('span:not(:first-child)') as HTMLElement
      expect(innerBar?.className).toContain('bg-(--ui-accent)')
      const dot = nowEl.querySelector('span:first-child') as HTMLElement
      expect(dot?.className).toContain('bg-(--ui-accent)')
      expect(nowEl.className).not.toContain('bg-(--ui-stroke-secondary)')
      // ...identical to TODAY's 10:00 tick, not tomorrow's.
      const today = new Date().toISOString().slice(0, 10)
      const todayTicks = screen
        .getAllByTestId('timeline-tick-10')
        .filter(node => node.closest(`[data-testid="timeline-day-${today}"]`))
      expect(todayTicks).toHaveLength(1)
      expect((todayTicks[0] as HTMLElement).getAttribute('style')).toBe(
        nowEl.getAttribute('style')
      )
      unmount()

      // Tomorrow's axis carries no "now" line — the line lives only inside
      // today's day container (裁定 36.2 / 39).
      render(<Timeline events={[event('b', dateAt(1, 9), { end_at: dateAt(1, 11) })]} />)
      const tomorrowDate = new Date()
      tomorrowDate.setDate(tomorrowDate.getDate() + 1)
      const tomorrowId = `timeline-day-${tomorrowDate.toISOString().slice(0, 10)}`
      expect(screen.getByTestId(tomorrowId).querySelector('[data-testid="timeline-now"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('always renders today first even when it has no rows (裁定 37.2 / 39)', () => {
    vi.useFakeTimers()

    try {
      vi.setSystemTime(new Date(2026, 8, 16, 9, 0, 0))
      // Only tomorrow has events. Today's axis must still appear above it
      // so the user can see "now" against an empty schedule.
      const today = new Date().toISOString().slice(0, 10)
      const tomorrowDate = new Date()
      tomorrowDate.setDate(tomorrowDate.getDate() + 1)

      render(
        <Timeline
          events={[event('only-tomorrow', dateAt(1, 10), { end_at: dateAt(1, 12) })]}
        />
      )

      const dayIds = screen.getAllByTestId(/^timeline-day-/).map(node => node.getAttribute('data-testid'))
      expect(dayIds[0]).toBe(`timeline-day-${today}`)
      expect(dayIds[1]).toBe(`timeline-day-${tomorrowDate.toISOString().slice(0, 10)}`)
      // The today axis is the minimum, so its section label still mounts.
      expect(screen.getAllByText('Today').length).toBeGreaterThan(0)
      // No events → no blocks; the avoid-band slot exists but stays empty.
      expect(screen.queryByTestId(/^timeline-avoid-/)).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('still renders the now line on an empty today axis (裁定 39)', () => {
    vi.useFakeTimers()

    try {
      vi.setSystemTime(new Date(2026, 8, 16, 10, 0, 0))
      render(<Timeline events={[event('b', dateAt(1, 14), { end_at: dateAt(1, 15) })]} />)
      // 10:00 → 2h past 08:00 = 88px.
      expect(topPxOf('timeline-now')).toBe(88)
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders confirmed avoid windows as faint full-width untitled bands', () => {
    render(
      <Timeline
        avoidWindows={[
          { end_at: dateAtTime(0, '13:00'), start_at: dateAtTime(0, '12:00') },
          { end_at: dateAtTime(0, '15:30'), start_at: dateAtTime(0, '14:30') }
        ]}
        events={[event('lecture', dateAt(0, 9), { end_at: dateAt(0, 11) })]}
      />
    )

    const bands = screen.getAllByTestId(/^timeline-avoid-/)
    expect(bands).toHaveLength(2)
    // 12:00 is 4h past 08:00 → 176px; 14:30 → 286px.
    expect(bands[0].getAttribute('style')).toMatch(/top:\s*176px/)
    expect(bands[1].getAttribute('style')).toMatch(/top:\s*286px/)
    // Untitled + non-interactive (the testid holds no children text).
    expect(bands[0].textContent).toBe('')
    expect(bands[0].className).toContain('pointer-events-none')
  })

  it('does not render avoid bands when the prop is omitted (old fixtures stay green)', () => {
    render(<Timeline events={[event('lecture', dateAt(0, 9), { end_at: dateAt(0, 11) })]} />)
    expect(screen.queryByTestId(/^timeline-avoid-/)).toBeNull()
  })

it('scrolls today into view inside a scrollable ancestor (裁定 40)', () => {
    vi.useFakeTimers()

    try {
      vi.setSystemTime(new Date(2026, 8, 16, 10, 0, 0))

      // jsdom returns zeros for getBoundingClientRect, so stub the rail /
      // today rects with plausible values: the rail's top is at 0, the today
      // axis sits at y=600. Without the effect, scrollTop stays at 0 and the
      // now line would be invisible. With the effect, the rail scrolls down.
      const fakeRailRect = { top: 0, bottom: 240, left: 0, right: 0, width: 0, height: 240, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
      const fakeTodayRect = { top: 600, bottom: 1304, left: 0, right: 0, width: 0, height: 704, x: 0, y: 600, toJSON: () => ({}) } as DOMRect
      const zeroRect = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect

      const original = Element.prototype.getBoundingClientRect
      Element.prototype.getBoundingClientRect = function patched() {
        const testId = (this as Element).getAttribute?.('data-testid')

        if (testId === 'fake-rail') {
          return fakeRailRect
        }

        if (testId === 'timeline-day-2026-09-16') {
          return fakeTodayRect
        }

        return zeroRect
      }

      render(
        <div data-testid="fake-rail" style={{ height: 240, overflowY: 'auto' }}>
          <Timeline events={[event('a', dateAt(0, 9), { end_at: dateAt(0, 11) })]} />
        </div>
      )

      // The effect runs synchronously after mount.
      expect(screen.getByTestId('fake-rail').scrollTop).toBeGreaterThan(0)

      Element.prototype.getBoundingClientRect = original
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops a duplicate event id from the rail so the same class never renders twice (WP-AXIS-INSIGHT)', () => {
    // Repro of the user's screenshot: the events prop carries the same id
    // twice (today's payload mirrored on top of the all-events window). The
    // dedupe step in <Timeline> must keep only the first occurrence so the
    // layout never greedy-splits it into a 50%-wide second column.
    const today = new Date().toISOString().slice(0, 10)
    const real = event('lecture', `${today}T18:30:00`, { end_at: `${today}T22:30:00` })
    const duped = [real, { ...real, title: 'lecture (mirrored from /api/agenda/day)' }]

    render(<Timeline events={duped} />)

    // Only one DOM node per id should survive.
    const matched = screen.getAllByTestId('timeline-block-lecture')
    expect(matched).toHaveLength(1)
  })

  it('tells a class, a routine anchor and planned work apart on one axis (裁定 34.3)', () => {
    render(
      <Timeline
        anchors={[{ end_at: dateAt(0, 13), label: '午饭', start_at: dateAt(0, 12) }]}
        events={[event('lecture', dateAt(0, 9), { end_at: dateAt(0, 11) })]}
        plans={[{ end_at: dateAt(0, 17), id: 'p1', lane: 'plan', start_at: dateAt(0, 14), title: '写文档' }]}
      />
    )

    const nodeOf = (text: string) => screen.getByText(text).closest('[data-lane]') as HTMLElement
    const lecture = nodeOf('event-lecture')
    const lunch = nodeOf('午饭')
    const plan = nodeOf('写文档')

    // Identity: three different lanes...
    expect([lecture, lunch, plan].map(node => node.getAttribute('data-lane'))).toEqual(['event', 'anchor', 'plan'])
    // ...rendered with three different skins (class strings must not collide).
    expect(new Set([lecture.className, lunch.className, plan.className]).size).toBe(3)

    // Anchor = dashed background band; plan = dotted fill + accent left rail.
    expect(lunch.className).toContain('border-dashed')
    expect(plan.className).toContain('border-dotted')
    expect(plan.className).toContain('border-l-(--ui-accent)')
    expect(lecture.className).not.toContain('border-dashed')
    expect(lecture.className).not.toContain('border-dotted')
    expect(lecture.className).not.toContain('border-l-(--ui-accent)')
  })
})

describe('shouldRefreshAgendaOnBusyChange (WP-G2)', () => {
  it('fires only on the busy → idle falling edge', () => {
    expect(shouldRefreshAgendaOnBusyChange(true, false)).toBe(true)
    expect(shouldRefreshAgendaOnBusyChange(false, false)).toBe(false)
    expect(shouldRefreshAgendaOnBusyChange(false, true)).toBe(false)
    expect(shouldRefreshAgendaOnBusyChange(true, true)).toBe(false)
  })
})
