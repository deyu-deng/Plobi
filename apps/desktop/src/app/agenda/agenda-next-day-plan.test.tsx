import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { $agendaError, $agendaEvents, $agendaLoading, $agendaSelectedId } from '@/store/agenda'
import type { AgendaEvent } from '@/types/plobi'

/**
 * 裁定 140 ②'s second criterion, taken literally: **the agenda render layer has a test that reads
 * 「明天」**. Not the unit of a helper — the board itself, on a real tomorrow computed from the same
 * local-day rule the board uses, showing the plan the 20:00 job generated for it.
 */

const notify = vi.hoisted(() => vi.fn())
const notifyError = vi.hoisted(() => vi.fn())

vi.mock('@/store/notifications', () => ({
  notify: (...args: unknown[]) => notify(...args),
  notifyError: (...args: unknown[]) => notifyError(...args)
}))

const selectDesktopPaths = vi.hoisted(() => vi.fn())

vi.mock('@/lib/desktop-fs', () => ({
  selectDesktopPaths: (...args: unknown[]) => selectDesktopPaths(...args)
}))

const getTimetable = vi.hoisted(() => vi.fn())
const previewTimetable = vi.hoisted(() => vi.fn())
const importTimetable = vi.hoisted(() => vi.fn())

vi.mock('./timetable/api', () => ({
  getTimetable: (...args: unknown[]) => getTimetable(...args),
  importTimetable: (...args: unknown[]) => importTimetable(...args),
  previewTimetable: (...args: unknown[]) => previewTimetable(...args)
}))

const getTalkerCollection = vi.hoisted(() => vi.fn())
const setTalkerMode = vi.hoisted(() => vi.fn())
const completeReview = vi.hoisted(() => vi.fn())

vi.mock('./talker/api', () => ({
  completeReview: (...args: unknown[]) => completeReview(...args),
  getTalkerCollection: (...args: unknown[]) => getTalkerCollection(...args),
  setTalkerMode: (...args: unknown[]) => setTalkerMode(...args)
}))

const getAgenda = vi.hoisted(() => vi.fn())
const confirmAgendaEvent = vi.hoisted(() => vi.fn())
const createAgendaEvent = vi.hoisted(() => vi.fn())
const deleteAgendaEvent = vi.hoisted(() => vi.fn())
const dismissAgendaEvent = vi.hoisted(() => vi.fn())
const updateAgendaEvent = vi.hoisted(() => vi.fn())

vi.mock('@/plobi', () => ({
  confirmAgendaEvent: (...args: unknown[]) => confirmAgendaEvent(...args),
  createAgendaEvent: (...args: unknown[]) => createAgendaEvent(...args),
  deleteAgendaEvent: (...args: unknown[]) => deleteAgendaEvent(...args),
  dismissAgendaEvent: (...args: unknown[]) => dismissAgendaEvent(...args),
  getAgenda: (...args: unknown[]) => getAgenda(...args),
  updateAgendaEvent: (...args: unknown[]) => updateAgendaEvent(...args)
}))

import { AgendaView } from './index'

/** Local tomorrow, built the way the board builds it — never `toISOString()` (that is the UTC day). */
function localKey(offsetDays: number): string {
  const date = new Date()

  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() + offsetDays)

  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function eventOn(dayKey: string): AgendaEvent {
  return {
    created_at: `${dayKey}T00:00:00`,
    end_at: null,
    id: `evt-${dayKey}`,
    kind: 'task',
    source: 'manual',
    start_at: `${dayKey}T09:00:00`,
    status: 'confirmed',
    title: `事情 ${dayKey}`,
    updated_at: `${dayKey}T00:00:00`
  }
}

let restoreApi: (() => void) | null = null

describe('AgendaView shows the next-day plan (裁定 140)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $agendaEvents.set([])
    $agendaLoading.set(false)
    $agendaError.set(null)
    $agendaSelectedId.set(null)
    getAgenda.mockResolvedValue([])
    getTalkerCollection.mockResolvedValue({ reviewComplete: false, talkers: [] })
    getTimetable.mockResolvedValue(null)

    const tomorrow = localKey(1)

    const api = vi.fn(async ({ path }: { path: string }) => {
      // Only the day endpoint exists for this purpose; anything else answers "no plan".
      if (path.includes(`date=${tomorrow}`)) {
        return {
          conflicts: [],
          date: tomorrow,
          events: [eventOn(tomorrow)],
          plan_items: [
            { end_at: `${tomorrow}T11:00:00`, id: 'pl-1', lane: 'plan', start_at: `${tomorrow}T10:00:00`, title: '明早的推进块' }
          ],
          plan_status: 'pending'
        }
      }

      return { conflicts: [], date: tomorrow, events: [], plan_items: [], plan_status: null }
    })

    const previous = (window as { plobiDesktop?: unknown }).plobiDesktop

    Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: { api } })

    restoreApi = () => {
      if (previous) {
        Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: previous })
      } else {
        Reflect.deleteProperty(window, 'plobiDesktop')
      }
    }
  })

  afterEach(() => {
    restoreApi?.()
    restoreApi = null
    cleanup()
  })

  it('renders a 明天 plan section carrying the backend items, without claiming any delivery', async () => {
    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <AgendaView onClose={() => undefined} />
      </I18nProvider>
    )

    const tomorrow = localKey(1)

    await waitFor(() => {
      expect(screen.queryByTestId('day-plan-明天')).not.toBeNull()
    })

    const box = screen.getByTestId('day-plan-明天')

    expect(box.textContent).toContain(`明天`)
    expect(box.textContent).toContain('10:00–11:00')
    expect(box.textContent).toContain('明早的推进块')
    expect(box.textContent).toContain('待你批准')
    // The week column for that same day reads the answered, clean conflict layer…
    expect(
      screen.getByTestId('agenda-week-strip').querySelector(`[data-day="${tomorrow}"]`)?.textContent
    ).toContain('无冲突')
    // …and today, which has no plan, gets no empty header.
    expect(screen.queryAllByTestId('day-plan-今天')).toHaveLength(0)
    // Nothing here says "pushed": the delivery field is the backend's, untouched.
    expect(box.textContent).not.toMatch(/已推送|delivered/i)
  })
})
