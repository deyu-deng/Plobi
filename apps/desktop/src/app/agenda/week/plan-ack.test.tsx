/**
 * WP-PLAN-COMPLETION-ACK 判据③（界面上那一下承认动作）的两条钉子。
 * 跑法：`npm run test:ui`，或 `vitest run --no-file-parallelism src/app/agenda/week/plan-ack.test.tsx`。
 *
 * 钉的是形状不是读数：
 * ① 计划行带 `event_id` ⇒ 有地方可以承认"这条做完了"，点下去打的是**那条事件**（完成态的真源在事件上，
 *    不在会被每晚重排抹掉的 `plan_items` 上），并把服务端那一行原样写回缓存；
 * ② 不带 `event_id` ⇒ **不画那颗按钮**。认不了就说认不了，不许拿标题去猜一条事件。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setAgendaEventCompleted = vi.hoisted(() => vi.fn())
const upsertAgendaEvent = vi.hoisted(() => vi.fn())

vi.mock('@/plobi', () => ({ setAgendaEventCompleted: (...args: unknown[]) => setAgendaEventCompleted(...args) }))
vi.mock('@/store/agenda', () => ({
  $completedEventIds: { store: 'completed' },
  $agendaEventIds: { store: 'known' },
  upsertAgendaEvent: (...args: unknown[]) => upsertAgendaEvent(...args)
}))
// 两格都从 store 里读：完成态这一趟还没有任何一条被认过；而 `evt_1` **在服务端答过的那份
// events 里**（WP-PLAN-ACK-NO-FAKE-CHECK 加的那道判定），所以这颗勾该画。
vi.mock('@nanostores/react', () => ({
  useStore: (store: { store?: string }) =>
    store?.store === 'known' ? new Set(['evt_1']) : new Set<string>()
}))

import { TRANSLATIONS } from '@/i18n/catalog'

import { DayPlanSection } from './day-plan-section'
import type { DayPlan } from './week-snapshots'

const en = TRANSLATIONS.en

const ROW = {
  end_at: null,
  event_id: 'evt_1',
  id: 'pi_1',
  kind: 'task',
  lane: 'plan',
  start_at: '2026-10-17T09:00:00',
  title: '电工电子学作业'
}

function plan(items: DayPlan['items']): DayPlan {
  return { items, status: 'confirmed' }
}

beforeEach(() => {
  setAgendaEventCompleted.mockReset()
  upsertAgendaEvent.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('晚间计划那一行的承认动作（判据③）', () => {
  it('带 event_id 的行认得出去：打的是那条事件，服务端那一行写回缓存', async () => {
    const echoed = { ...ROW, completed_at: '2026-10-16T11:00:00' }

    setAgendaEventCompleted.mockResolvedValue(echoed)

    render(<DayPlanSection dayLabel="明天" plan={plan([ROW] as never)} translations={en} />)

    const button = await screen.findByLabelText('Mark done')

    fireEvent.click(button)

    await waitFor(() => expect(setAgendaEventCompleted).toHaveBeenCalledWith('evt_1', true))
    expect(upsertAgendaEvent).toHaveBeenCalledWith(echoed)
  })

  it('没有 event_id 就不画那颗按钮：认不了直说，不拿标题去猜一条事件', async () => {
    const { event_id: _dropped, ...row } = ROW

    render(<DayPlanSection dayLabel="明天" plan={plan([row] as never)} translations={en} />)

    expect((await screen.findByText(/电工电子学作业/)).textContent).toContain('电工电子学作业')
    expect(screen.queryByLabelText('Mark done')).toBeNull()
    expect(setAgendaEventCompleted).not.toHaveBeenCalled()
  })
})
