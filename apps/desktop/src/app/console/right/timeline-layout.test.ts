import { describe, expect, it } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import {
  dayKeysOf,
  emptyDay,
  HOUR_HEIGHT,
  layoutDay,
  MIN_BLOCK_HEIGHT,
  minutesOf
} from './timeline-layout'

const DAY = '2026-09-11'

function event(id: string, startAt: string, endAt: null | string = null, status: AgendaEvent['status'] = 'confirmed'): AgendaEvent {
  const now = '2026-09-10T00:00:00Z'

  return {
    confirm_seq: null,
    created_at: now,
    end_at: endAt,
    id,
    kind: 'task',
    source: 'manual',
    start_at: startAt,
    status,
    title: `event-${id}`,
    updated_at: now
  }
}

describe('layoutDay', () => {
  it('returns null for a day with no events', () => {
    expect(layoutDay([event('a', `${DAY}T09:00:00`)], '2026-09-12')).toBeNull()
  })

  it('always exposes at least the 08:00–23:00 tick marks', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY)

    expect(day).not.toBeNull()
    expect(day?.hours[0]).toBe(8)
    expect(day?.hours.at(-1)).toBe(23)
    expect(day?.spanStartMinutes).toBe(8 * 60)
    expect(day?.spanEndMinutes).toBe(24 * 60)
  })

  it('sizes a block from start_at to end_at', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY)
    const block = day?.blocks[0]

    expect(block?.source && 'start_at' in block.source ? block.source.start_at.slice(11, 16) : '').toBe('09:00')
    expect(block?.source && 'end_at' in block.source ? block.source.end_at?.slice(11, 16) : null).toBe('11:00')
    expect(block?.openEnded).toBe(false)
    // Two hours → two hour-heights; 09:00 is one hour past the 08:00 axis start.
    expect(block?.heightPx).toBe(2 * HOUR_HEIGHT)
    expect(block?.topPx).toBe(HOUR_HEIGHT)
  })

  it('never invents an end: a missing end_at becomes a minimum-height marker', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`)], DAY)
    const block = day?.blocks[0]

    expect(block?.openEnded).toBe(true)
    expect(block?.endClock).toBeNull()
    expect(block?.heightPx).toBe(MIN_BLOCK_HEIGHT)
  })

  it('widens the axis for entries outside 08:00–23:00', () => {
    const early = layoutDay([event('a', `${DAY}T06:30:00`, `${DAY}T07:00:00`)], DAY)
    const late = layoutDay([event('b', `${DAY}T23:40:00`, `${DAY}T23:59:00`)], DAY)

    expect(early?.spanStartMinutes).toBe(6 * 60)
    expect(late?.spanEndMinutes).toBeGreaterThan(24 * 60 - 1)
    // Even widened, the tick marks stay on whole hours inside the span.
    expect(late?.hours.at(-1)).toBe(23)
  })

  it('sorts blocks by start and gives overlaps their own column', () => {
    const day = layoutDay(
      [
        event('late', `${DAY}T15:00:00`, `${DAY}T16:00:00`),
        event('early', `${DAY}T09:00:00`, `${DAY}T10:00:00`),
        event('clash', `${DAY}T09:30:00`, `${DAY}T10:30:00`)
      ],
      DAY
    )

    const blocks = (day?.blocks ?? []).filter(block => block.lane === 'event')
    expect(blocks.map(block => block.id)).toEqual(['early', 'clash', 'late'])
    expect(blocks.map(block => block.column)).toEqual([0, 1, 0])
  })

  it('flags pending blocks so the rail can keep them amber', () => {
    const day = layoutDay([event('p', `${DAY}T10:00:00`, null, 'pending')], DAY)

    expect(day?.blocks[0].status).toBe('pending')
  })

  it('places two overlapping events into different columns with widthFraction 0.5 (R-031.1)', () => {
    const day = layoutDay(
      [
        event('left', `${DAY}T14:00:00`, `${DAY}T16:00:00`),
        event('right', `${DAY}T15:00:00`, `${DAY}T17:00:00`)
      ],
      DAY
    )

    const blocks = (day?.blocks ?? []).filter(block => block.lane === 'event')
    const sorted = blocks.slice().sort((a, b) => a.startMinutes - b.startMinutes)

    expect(sorted.map(block => block.id)).toEqual(['left', 'right'])
    expect(sorted.map(block => block.column)).toEqual([0, 1])
    expect(sorted.every(block => block.overlaps)).toBe(true)
    expect(sorted[0].widthFraction).toBeCloseTo(0.5)
    expect(sorted[1].widthFraction).toBeCloseTo(0.5)
  })

  it('does not let an open-ended block steal a column from overlapping timed events', () => {
    const day = layoutDay(
      [event('pinned', `${DAY}T15:00:00`), event('meeting', `${DAY}T15:30:00`, `${DAY}T16:30:00`)],
      DAY
    )

    const blocks = (day?.blocks ?? []).filter(block => block.lane === 'event')
    const pinned = blocks.find(block => block.id === 'pinned')
    const meeting = blocks.find(block => block.id === 'meeting')

    expect(pinned?.column).toBe(0)
    expect(pinned?.overlaps).toBe(false)
    expect(pinned?.widthFraction).toBe(1)
    expect(meeting?.column).toBe(0)
    expect(meeting?.widthFraction).toBe(1)
  })

  it('lays every lane out on ONE grid: a colliding class and breakfast split columns (裁定 36.2)', () => {
    const day = layoutDay(
      [event('lecture', `${DAY}T08:00:00`, `${DAY}T09:35:00`)],
      DAY,
      [{ end_at: `${DAY}T08:20:00`, label: '早餐', start_at: `${DAY}T07:40:00` }]
    )

    const blocks = day?.blocks ?? []
    const lecture = blocks.find(block => block.lane === 'event' && block.id === 'lecture')
    const breakfast = blocks.find(block => block.lane === 'anchor')

    // Breakfast starts first, so it owns column 0; the lecture moves beside it.
    expect(breakfast?.column).toBe(0)
    expect(lecture?.column).toBe(1)
    expect(lecture?.widthFraction).toBeCloseTo(0.5)
    expect(breakfast?.widthFraction).toBeCloseTo(0.5)
    expect(lecture?.overlaps).toBe(true)
    expect(breakfast?.overlaps).toBe(true)
  })

  it('keeps every lane that collides with nothing at full width', () => {
    const day = layoutDay(
      [event('lecture', `${DAY}T09:00:00`, `${DAY}T11:00:00`)],
      DAY,
      [{ end_at: `${DAY}T13:00:00`, label: '午饭', start_at: `${DAY}T12:00:00` }],
      [{ end_at: `${DAY}T18:00:00`, id: 'p1', lane: 'plan', start_at: `${DAY}T16:00:00`, title: '写文档' }]
    )

    const blocks = day?.blocks ?? []
    const lecture = blocks.find(block => block.lane === 'event' && block.id === 'lecture')
    const lunch = blocks.find(block => block.lane === 'anchor')
    const plan = blocks.find(block => block.lane === 'plan')

    expect(lecture?.lane).toBe('event')
    expect(lunch?.title).toBe('午饭')
    expect(plan?.title).toBe('写文档')
    // Three separate clusters → three column-0 blocks at full width.
    expect([lecture, lunch, plan].map(block => block?.column)).toEqual([0, 0, 0])
    expect([lecture, lunch, plan].map(block => block?.widthFraction)).toEqual([1, 1, 1])
    expect(blocks.every(block => block.overlaps === false)).toBe(true)
  })

  it('keeps non-overlapping routines in one full-width column (裁定 32.1)', () => {
    const day = layoutDay([], DAY, [
      { end_at: `${DAY}T13:00:00`, label: '午饭', start_at: `${DAY}T12:00:00` },
      { end_at: `${DAY}T19:00:00`, label: '晚饭', start_at: `${DAY}T18:00:00` }
    ])

    const routines = (day?.blocks ?? []).filter(block => block.lane === 'anchor')

    expect(routines.map(block => block.title)).toEqual(['午饭', '晚饭'])
    // One column, full width — not two 50% slivers stacked at the axis top.
    expect(routines.every(block => block.column === 0)).toBe(true)
    expect(routines.every(block => block.widthFraction)).toBe(true)
    expect(routines.every(block => block.overlaps === false)).toBe(true)
  })

  it('splits only the routines that really collide', () => {
    const day = layoutDay([], DAY, [
      { end_at: `${DAY}T13:00:00`, label: '午饭', start_at: `${DAY}T12:00:00` },
      { end_at: `${DAY}T13:30:00`, label: '吃药', start_at: `${DAY}T12:30:00` },
      { end_at: `${DAY}T19:00:00`, label: '晚饭', start_at: `${DAY}T18:00:00` }
    ])

    const routines = (day?.blocks ?? []).filter(block => block.lane === 'anchor')
    const lunch = routines.find(block => block.title === '午饭')
    const pills = routines.find(block => block.title === '吃药')
    const dinner = routines.find(block => block.title === '晚饭')

    expect(lunch?.column).toBe(0)
    expect(pills?.column).toBe(1)
    expect(dinner?.column).toBe(0)
    expect(lunch?.overlaps).toBe(true)
    expect(pills?.overlaps).toBe(true)
    expect(dinner?.overlaps).toBe(false)
    // The clash pair splits; the unrelated dinner keeps the whole width
    // (per-cluster counts, 裁定 36.2).
    expect(lunch?.widthFraction).toBeCloseTo(0.5)
    expect(pills?.widthFraction).toBeCloseTo(0.5)
    expect(dinner?.widthFraction).toBe(1)
  })

  it('draws a cross-midnight sleep down to the axis bottom and puts a late event beside it', () => {
    const day = layoutDay(
      [event('call', `${DAY}T23:00:00`, `${DAY}T23:45:00`)],
      DAY,
      [{ end_at: `${DAY}T07:00:00`, label: '睡眠', start_at: `${DAY}T22:00:00` }]
    )

    const blocks = day?.blocks ?? []
    const sleep = blocks.find(block => block.lane === 'anchor')
    const call = blocks.find(block => block.id === 'call')

    expect(sleep?.heightPx).toBe(2 * HOUR_HEIGHT)
    expect(sleep?.column).toBe(0)
    expect(call?.column).toBe(1)
    expect(call?.overlaps).toBe(true)
  })

  it('flags only the events whose intervals intersect (裁定 32.1)', () => {
    const day = layoutDay(
      [
        event('morning', `${DAY}T09:00:00`, `${DAY}T10:00:00`),
        event('left', `${DAY}T14:00:00`, `${DAY}T16:00:00`),
        event('right', `${DAY}T15:00:00`, `${DAY}T17:00:00`)
      ],
      DAY
    )

    const blocks = (day?.blocks ?? []).filter(block => block.lane === 'event')
    const flagged = blocks.filter(block => block.overlaps).map(block => block.id)

    // The 09:00 lecture shares no minute with the 14:00/15:00 clash.
    expect(flagged).toEqual(['left', 'right'])
    expect(blocks.find(block => block.id === 'morning')?.column).toBe(0)
  })

  it('puts a late block on the 18:30 tick, not at the axis top (裁定 32.1)', () => {
    const day = layoutDay([event('evening', `${DAY}T18:30:00`, `${DAY}T22:30:00`)], DAY)
    const block = day?.blocks[0]

    // 10.5 hours past the 08:00 axis start.
    expect(block?.topPx).toBe(10.5 * HOUR_HEIGHT)
    expect(block?.topPx).toBe(462)
    expect(block?.heightPx).toBe(4 * HOUR_HEIGHT)
    expect(day?.spanStartMinutes).toBe(8 * 60)
  })

  it('draws a cross-midnight row down to the axis bottom instead of going negative', () => {
    const day = layoutDay([], DAY, [{ end_at: `${DAY}T07:00:00`, label: '睡眠', start_at: `${DAY}T22:00:00` }])
    const block = (day?.blocks ?? []).find(row => row.lane === 'anchor')

    expect(block?.topPx).toBe((22 * 60 - 8 * 60) / 60 * HOUR_HEIGHT)
    // 22:00 → axis bottom (24:00) = 2h, NOT max(MIN, negative).
    expect(block?.heightPx).toBe(2 * HOUR_HEIGHT)
    expect(block?.heightPx).toBeGreaterThan(0)
  })
})

describe('dayKeysOf / minutesOf', () => {
  it('lists event days chronologically without duplicates', () => {
    expect(dayKeysOf([event('a', `${DAY}T09:00:00`), event('b', `${DAY}T18:00:00`), event('c', '2026-09-12T08:00:00')])).toEqual([
      DAY,
      '2026-09-12'
    ])
  })

  it('parses the clock part defensively', () => {
    expect(minutesOf(`${DAY}T09:30:00`)).toBe(9 * 60 + 30)
    expect(minutesOf('')).toBe(0)
    expect(minutesOf('nonsense')).toBe(0)
  })
})

describe('avoid_windows (裁定 39 / WP-AXIS-TODAY)', () => {
  it('renders a confirmed avoid window as a topPx / heightPx band', () => {
    const day = layoutDay(
      [],
      DAY,
      [],
      [],
      [{ end_at: `${DAY}T13:00:00`, start_at: `${DAY}T12:00:00` }]
    )

    expect(day?.avoidBands).toHaveLength(1)
    const [band] = day?.avoidBands ?? []
    // 12:00 is 4h past the 08:00 axis start at 44px/h.
    expect(band?.topPx).toBe(4 * HOUR_HEIGHT)
    expect(band?.heightPx).toBe(HOUR_HEIGHT)
  })

  it('returns null for a day with only no rows and no avoid windows', () => {
    expect(layoutDay([], DAY, [], [], [])).toBeNull()
  })

  it('keeps an empty avoid list a no-op', () => {
    const day = layoutDay([event('lecture', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY, [], [], [])
    expect(day?.avoidBands).toEqual([])
  })
})

describe('emptyDay (裁定 37.2 — today axis even with zero rows)', () => {
  it('returns the 08:00–23:00 ticks with no blocks or avoid bands', () => {
    const day = emptyDay('2026-09-16')

    expect(day.hours[0]).toBe(8)
    expect(day.hours.at(-1)).toBe(23)
    expect(day.blocks).toEqual([])
    expect(day.avoidBands).toEqual([])
  })
})
