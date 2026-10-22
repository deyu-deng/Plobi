import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { setAgendaEvents } from '@/store/agenda'
import { $notifications } from '@/store/notifications'
import type { AgendaEvent } from '@/types/plobi'

import { DayPlanSection } from './day-plan-section'
import type { DayPlan } from './week-snapshots'

const setAgendaEventCompleted = vi.hoisted(() => vi.fn())

vi.mock('@/plobi', () => ({ setAgendaEventCompleted: (...args: unknown[]) => setAgendaEventCompleted(...args) }))

const zh = TRANSLATIONS.zh
const en = TRANSLATIONS.en

function plan(overrides: Partial<DayPlan> = {}): DayPlan {
  return {
    items: [
      { end_at: '2026-10-15T11:00:00', id: 'p1', lane: 'plan', start_at: '2026-10-15T10:00:00', title: '理论力学习作' },
      { end_at: null, id: 'p2', lane: 'plan', start_at: '2026-10-15T14:00:00', title: 'Resonote 推进块' }
    ],
    status: 'pending',
    ...overrides
  }
}

afterEach(() => {
  cleanup()
})

describe('DayPlanSection (裁定 140)', () => {
  it('renders tomorrow’s plan under a 明天 header, with times the backend gave', () => {
    const view = render(<DayPlanSection dayLabel="明天" plan={plan()} translations={zh} />)
    const box = view.getByTestId('day-plan-明天')

    expect(box.textContent).toContain('明天的计划')
    expect(box.textContent).toContain('待你批准')
    expect(box.textContent).toContain('10:00–11:00 理论力学习作')
    // An item with no end is a point: print the start, never invent a duration.
    expect(box.textContent).toContain('14:00 Resonote 推进块')
    expect(box.textContent).not.toContain('14:00–')
  })

  it('says nothing at all when that day has no plan', () => {
    const view = render(<DayPlanSection dayLabel="明天" plan={undefined} translations={zh} />)

    expect(view.queryByTestId('day-plan-明天')).toBeNull()
    expect(view.container.textContent).toBe('')
  })

  it('distinguishes "answered, empty" from "not answered"', () => {
    const view = render(<DayPlanSection dayLabel="今天" plan={plan({ items: [], status: null })} translations={zh} />)

    expect(view.getByTestId('day-plan-今天').textContent).toContain('这一天没有生成计划')
    expect(view.getByTestId('day-plan-今天').textContent).toContain('无状态')
  })

  it('falls back to the raw status rather than inventing one, and keeps English labels intact', () => {
    const view = render(<DayPlanSection dayLabel="Tomorrow" plan={plan({ status: 'approved' })} translations={en} />)
    const box = view.getByTestId('day-plan-Tomorrow')

    expect(box.textContent).toContain('Plan for Tomorrow')
    expect(box.textContent).toContain('approved')
    expect(box.textContent).not.toContain('awaiting your confirmation')
  })
})

describe('DayPlanSection through the default context', () => {
  it('renders without a provider (jsdom default is English)', () => {
    render(<DayPlanSection dayLabel="Today" plan={plan()} />)

    expect(screen.getByTestId('day-plan-Today').textContent).toContain('Plan for Today')
  })
})

/**
 * WP-PLAN-ACK-NO-FAKE-CHECK — 今天那份 13 行里 12 行的 `event_id` 是排程自造的
 * （`l1:block-3`），`events` 表里查无此行；点下去后端
 * `EventNotFound` → 404 → 一条「操作失败」。画一颗只会报错的勾比不画更坏。
 */
function event(id: string): AgendaEvent {
  return {
    created_at: '2026-10-18T00:00:00',
    id,
    kind: 'task',
    source: 'manual',
    start_at: '2026-10-18T10:00:00',
    status: 'confirmed',
    title: id
  } as AgendaEvent
}

function ackRow(eventId: null | string, id = 'pi_1'): never {
  return {
    end_at: null,
    ...(eventId ? { event_id: eventId } : {}),
    id,
    kind: 'task',
    lane: 'plan',
    start_at: '2026-10-18T10:00:00',
    title: '第5次课后作业'
  } as never
}

describe('那一颗勾不许是假的（WP-PLAN-ACK-NO-FAKE-CHECK）', () => {
  beforeEach(() => {
    setAgendaEventCompleted.mockReset()
    setAgendaEvents([])
    $notifications.set([])
  })

  afterEach(cleanup)

  it('event_id 有值但服务端那份 events 里查无此行 ⇒ 不画可点的勾，直说记不了', () => {
    setAgendaEvents([event('evt_real')])

    render(<DayPlanSection dayLabel="今天" plan={plan({ items: [ackRow('l1:block-3')] })} translations={en} />)

    expect(screen.queryByLabelText('Mark done')).toBeNull()
    expect(screen.getByTestId('unrecordable-pi_1').textContent).toBe(en.agenda.plan.unackable)
  })

  it('那颗 id 真在 events 里 ⇒ 勾照旧画，别把点得动的也一起藏了', () => {
    setAgendaEvents([event('evt_real')])

    render(<DayPlanSection dayLabel="今天" plan={plan({ items: [ackRow('evt_real')] })} translations={en} />)

    expect(screen.getByLabelText('Mark done')).toBeTruthy()
    expect(screen.queryByTestId('unrecordable-pi_1')).toBeNull()
  })

  it('服务端还没答 ⇒ 既不画勾也不说「记不了」：空缓存不是一条事实', () => {
    setAgendaEvents([])

    render(<DayPlanSection dayLabel="今天" plan={plan({ items: [ackRow('l1:block-3')] })} translations={en} />)

    expect(screen.queryByLabelText('Mark done')).toBeNull()
    expect(screen.queryByTestId('unrecordable-pi_1')).toBeNull()
  })

  it('点得动那一路要么成功要么明确报错，不许静默无反应', async () => {
    setAgendaEvents([event('evt_real')])
    setAgendaEventCompleted.mockRejectedValue(new Error('Request failed: 404'))

    render(<DayPlanSection dayLabel="今天" plan={plan({ items: [ackRow('evt_real')] })} translations={en} />)

    fireEvent.click(screen.getByLabelText('Mark done'))

    await waitFor(() => expect($notifications.get().some(n => n.kind === 'error')).toBe(true))
    expect($notifications.get().find(n => n.kind === 'error')?.title).toBe(en.agenda.actionFailed)
  })
})
