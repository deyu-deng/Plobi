import { describe, expect, it } from 'vitest'

import type { AgendaEvent } from '@/types/plobi'

import {
  dayKeysOf,
  detailRowsForHeight,
  detailRowsOf,
  emptyDay,
  HOUR_HEIGHT,
  layoutDay,
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

  it('always exposes the whole day: the 00:00 axis through the 23:00 tick', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY)

    expect(day).not.toBeNull()
    expect(day?.hours[0]).toBe(0)
    expect(day?.hours.at(-1)).toBe(23)
    expect(day?.spanStartMinutes).toBe(0)
    expect(day?.spanEndMinutes).toBe(24 * 60)
  })

  it('sizes a block from start_at to end_at', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY)
    const block = day?.blocks[0]

    expect(block?.source && 'start_at' in block.source ? block.source.start_at.slice(11, 16) : '').toBe('09:00')
    expect(block?.source && 'end_at' in block.source ? block.source.end_at?.slice(11, 16) : null).toBe('11:00')
    expect(block?.openEnded).toBe(false)
    // 该多长就多长：两小时就是两个格高；09:00 在零点轴往下第九格。
    expect(block?.heightPx).toBe(2 * HOUR_HEIGHT)
    expect(block?.topPx).toBe(9 * HOUR_HEIGHT)
  })

  it('never invents an end: a missing end_at is a line, not a made-up hour', () => {
    const day = layoutDay([event('a', `${DAY}T09:00:00`)], DAY)
    const block = day?.blocks[0]

    expect(block?.openEnded).toBe(true)
    expect(block?.endClock).toBeNull()
    // 零高度＝渲染层画一条横贯当天的线（用户 2026-10-21：deadline 就是一条线）。
    expect(block?.heightPx).toBe(0)
  })

  it('keeps one fixed axis for every day: early and late rows never rescale it', () => {
    const early = layoutDay([event('a', `${DAY}T06:30:00`, `${DAY}T07:00:00`)], DAY)
    const late = layoutDay([event('b', `${DAY}T23:40:00`, `${DAY}T23:59:00`)], DAY)

    expect(early?.spanStartMinutes).toBe(0)
    expect(early?.blocks[0]?.topPx).toBe(6.5 * HOUR_HEIGHT)
    expect(late?.spanEndMinutes).toBe(24 * 60)
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

    const blocks = (day?.blocks ?? [])
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

    const blocks = (day?.blocks ?? [])
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

    const blocks = (day?.blocks ?? [])
    const pinned = blocks.find(block => block.id === 'pinned')
    const meeting = blocks.find(block => block.id === 'meeting')

    expect(pinned?.column).toBe(0)
    expect(pinned?.overlaps).toBe(false)
    expect(pinned?.widthFraction).toBe(1)
    expect(meeting?.column).toBe(0)
    expect(meeting?.widthFraction).toBe(1)
  })

  it('draws a cross-midnight row down to the axis bottom and puts a late event beside it', () => {
    const day = layoutDay(
      [
        event('sleep', `${DAY}T22:00:00`, `${DAY}T07:00:00`),
        event('call', `${DAY}T23:00:00`, `${DAY}T23:45:00`)
      ],
      DAY
    )

    const blocks = day?.blocks ?? []
    const sleep = blocks.find(block => block.id === 'sleep')
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

    const blocks = (day?.blocks ?? [])
    const flagged = blocks.filter(block => block.overlaps).map(block => block.id)

    // The 09:00 lecture shares no minute with the 14:00/15:00 clash.
    expect(flagged).toEqual(['left', 'right'])
    expect(blocks.find(block => block.id === 'morning')?.column).toBe(0)
  })

  it('puts a late block on the 18:30 tick, not at the axis top (裁定 32.1)', () => {
    const day = layoutDay([event('evening', `${DAY}T18:30:00`, `${DAY}T22:30:00`)], DAY)
    const block = day?.blocks[0]

    // 轴从 00:00 起：18:30 就是第 18.5 格。
    expect(block?.topPx).toBe(18.5 * HOUR_HEIGHT)
    expect(block?.topPx).toBe(814)
    expect(block?.heightPx).toBe(4 * HOUR_HEIGHT)
    expect(day?.spanStartMinutes).toBe(0)
  })

  it('draws a cross-midnight row down to the axis bottom instead of going negative', () => {
    const day = layoutDay([event('sleep', `${DAY}T22:00:00`, `${DAY}T07:00:00`)], DAY)
    const block = (day?.blocks ?? [])[0]

    expect(block?.topPx).toBe(22 * HOUR_HEIGHT)
    // 22:00 → 轴尾（24:00）= 两小时；负的算法早被夹掉了，这里也没有最小高度可撑。
    expect(block?.heightPx).toBe(2 * HOUR_HEIGHT)
    expect(block?.heightPx).toBeGreaterThan(0)
  })
})

describe('跨天事件（用户 10-31：睡觉那种要两头都显示）', () => {
  const NEXT = '2026-09-12'

  it('真 ISO 跨天：前一天铺到轴尾，第二天早上接着画', () => {
    const sleep = event('sleep', `${DAY}T23:00:00`, `${NEXT}T07:00:00`)

    const first = layoutDay([sleep], DAY)?.blocks[0]
    const second = layoutDay([sleep], NEXT)?.blocks[0]

    // 前一天 23:00 → 24:00 只有一小时。
    expect(first?.topPx).toBe(23 * HOUR_HEIGHT)
    expect(first?.heightPx).toBe(HOUR_HEIGHT)
    expect(first?.clippedEnd).toBe(true)
    expect(first?.clippedStart).toBe(false)

    // 第二天 00:00 → 07:00 从头开始，块顶贴着轴顶。
    expect(second?.topPx).toBe(0)
    expect(second?.heightPx).toBe(7 * HOUR_HEIGHT)
    expect(second?.clippedStart).toBe(true)
    expect(second?.clippedEnd).toBe(false)
  })

  it('单日写法（同一天写 23:00 → 07:00）也算跨天', () => {
    const sleep = event('sleep', `${DAY}T23:00:00`, `${DAY}T07:00:00`)
    const second = layoutDay([sleep], NEXT)?.blocks[0]

    expect(second?.heightPx).toBe(7 * HOUR_HEIGHT)
    expect(second?.clippedStart).toBe(true)
  })

  it('跨好几天时两头都切，中间那天画满整根轴', () => {
    const trip = event('trip', '2026-09-10T20:00:00', '2026-09-13T09:00:00')
    const middle = layoutDay([trip], DAY)?.blocks[0]

    expect(middle?.topPx).toBe(0)
    expect(middle?.heightPx).toBe(24 * HOUR_HEIGHT)
    expect(middle?.clippedStart).toBe(true)
    expect(middle?.clippedEnd).toBe(true)
  })

  it('deadline 不跨天：没有结束时间就只落在它自己那一天', () => {
    const ddl = event('ddl', `${DAY}T23:00:00`)

    expect(layoutDay([ddl], NEXT)).toBeNull()
  })

  it('跨天那半截照样参与列切分（第二天早上有事不叠着画）', () => {
    const sleep = event('sleep', `${DAY}T23:00:00`, `${NEXT}T07:00:00`)
    const jog = event('jog', `${NEXT}T06:00:00`, `${NEXT}T07:30:00`)
    const blocks = layoutDay([sleep, jog], NEXT)?.blocks ?? []

    expect(blocks.map(block => block.id)).toEqual(['sleep', 'jog'])
    expect(blocks.map(block => block.column)).toEqual([0, 1])
    expect(blocks.every(block => block.overlaps)).toBe(true)
  })
})

describe('块内清单（用户 10-31）', () => {
  it('顺序是地点 → 未勾清单 → 备注，勾掉的清单不进块', () => {
    const row = event('a', `${DAY}T09:00:00`, `${DAY}T12:00:00`)

    row.location = '教三-201'
    row.details = [
      { done: true, id: 'd1', text: '已办完的' },
      { done: false, id: 'd2', text: '数据整理' }
    ]
    row.notes = '别忘带 U 盘'

    const block = layoutDay([row], DAY)?.blocks[0]
    const rows = detailRowsOf(block!, 3)

    expect(rows.map(item => item.kind)).toEqual(['location', 'detail', 'note'])
    expect(rows.map(item => item.text)).toEqual(['教三-201', '数据整理', '别忘带 U 盘'])
    // 勾完的不占块内空间。
    expect(block?.details.map(item => item.text)).toEqual(['数据整理'])
  })

  it('能写几行由块高决定：半小时只够标题，三小时能写满四行', () => {
    expect(detailRowsForHeight(22)).toBe(0)
    expect(detailRowsForHeight(HOUR_HEIGHT)).toBe(1)
    expect(detailRowsForHeight(3 * HOUR_HEIGHT)).toBe(3)
    // 再高也不超过四行（标题 + 三行正文），免得块里变成一篇文章。
    expect(detailRowsForHeight(10 * HOUR_HEIGHT)).toBe(3)
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
      [{ end_at: `${DAY}T13:00:00`, start_at: `${DAY}T12:00:00` }]
    )

    expect(day?.avoidBands).toHaveLength(1)
    const [band] = day?.avoidBands ?? []
    // 12:00 = 零点轴往下第 12 格。
    expect(band?.topPx).toBe(12 * HOUR_HEIGHT)
    expect(band?.heightPx).toBe(HOUR_HEIGHT)
  })

  it('returns null for a day with only no rows and no avoid windows', () => {
    expect(layoutDay([], DAY, [])).toBeNull()
  })

  it('keeps an empty avoid list a no-op', () => {
    const day = layoutDay([event('lecture', `${DAY}T09:00:00`, `${DAY}T11:00:00`)], DAY)
    expect(day?.avoidBands).toEqual([])
  })
})

describe('emptyDay (裁定 37.2 — today axis even with zero rows)', () => {
  it('returns the 00:00–23:00 ticks with no blocks or avoid bands', () => {
    const day = emptyDay('2026-09-16')

    expect(day.hours[0]).toBe(0)
    expect(day.hours.at(-1)).toBe(23)
    expect(day.blocks).toEqual([])
    expect(day.avoidBands).toEqual([])
  })
})
