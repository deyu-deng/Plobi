import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'

import { DayPlanSection } from './day-plan-section'
import type { DayPlan } from './week-snapshots'

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
