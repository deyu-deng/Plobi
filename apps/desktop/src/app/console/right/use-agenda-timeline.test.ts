/**
 * WP-AXIS-LOCAL (裁定 33.5): the right rail must ask the backend for the LOCAL
 * calendar day. `toISOString().slice(0, 10)` is UTC, so at UTC+8 every morning
 * before 08:00 the rail requested *yesterday* — meals/sleep from the previous
 * day while the events (fetched without a date) were today's.
 */

import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $agendaEvents } from '@/store/agenda'
import type { AgendaDay } from '@/types/plobi'

import { fetchAllAgendaEvents } from '../api'
import {
  $agendaAnchors,
  $agendaPlans,
  $agendaRhythmOff,
  localDateKey,
  useAgendaTimeline
} from './use-agenda-timeline'

vi.mock('../api', () => ({ fetchAllAgendaEvents: vi.fn().mockResolvedValue([]) }))

const DAY: AgendaDay = { anchors: [], date: '2026-09-16', events: [], plan_items: [] }

let restoreDesktopApi: (() => void) | null = null

function installDesktopApi() {
  const api = vi.fn().mockResolvedValue(DAY)
  const previous = (window as { plobiDesktop?: unknown }).plobiDesktop

  Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: { api } })

  restoreDesktopApi = () => {
    if (previous) {
      Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: previous })
    } else {
      Reflect.deleteProperty(window, 'plobiDesktop')
    }
  }

  return api
}

/** 2026-09-15T23:00Z — the 16th at 07:00 in UTC+8, still the 15th in UTC. */
const MORNING_IN_UTC_PLUS_8 = '2026-09-15T23:00:00Z'

function requestPaths(api: ReturnType<typeof vi.fn>): string[] {
  return api.mock.calls.map(call => String((call[0] as { path?: unknown } | undefined)?.path ?? ''))
}

describe('localDateKey (裁定 33.5)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reads the local calendar day, not the UTC day', () => {
    const instant = new Date(MORNING_IN_UTC_PLUS_8)

    vi.spyOn(instant, 'getFullYear').mockReturnValue(2026)
    vi.spyOn(instant, 'getMonth').mockReturnValue(8) // September (0-based)
    vi.spyOn(instant, 'getDate').mockReturnValue(16)

    // The trap this replaced: the UTC day is still the 15th.
    expect(instant.toISOString().slice(0, 10)).toBe('2026-09-15')
    expect(localDateKey(instant)).toBe('2026-09-16')
  })

  it('zero-pads single-digit months and days', () => {
    const date = new Date('2026-01-05T12:00:00Z')

    vi.spyOn(date, 'getFullYear').mockReturnValue(2026)
    vi.spyOn(date, 'getMonth').mockReturnValue(0)
    vi.spyOn(date, 'getDate').mockReturnValue(5)

    expect(localDateKey(date)).toBe('2026-01-05')
  })
})

describe('useAgendaTimeline day request (裁定 33.5)', () => {
  beforeEach(() => {
    $agendaEvents.set([])
    $agendaAnchors.set([])
    $agendaPlans.set([])
    $agendaRhythmOff.set(false)
    vi.mocked(fetchAllAgendaEvents).mockResolvedValue([])
  })

  afterEach(() => {
    restoreDesktopApi?.()
    restoreDesktopApi = null
    vi.restoreAllMocks()
  })

  it('asks for the local day even while the UTC clock is still yesterday', async () => {
    const utcNow = new Date(MORNING_IN_UTC_PLUS_8)

    vi.spyOn(Date.prototype, 'getFullYear').mockReturnValue(2026)
    vi.spyOn(Date.prototype, 'getMonth').mockReturnValue(8)
    vi.spyOn(Date.prototype, 'getDate').mockReturnValue(16)

    expect(utcNow.toISOString().slice(0, 10)).toBe('2026-09-15')

    const api = installDesktopApi()
    const { unmount } = renderHook(() => useAgendaTimeline(true))

    await waitFor(() => expect(api).toHaveBeenCalled())

    const dayPath = requestPaths(api).find(path => path.startsWith('/api/agenda/day'))

    expect(dayPath).toBe('/api/agenda/day?date=2026-09-16')
    expect(requestPaths(api).some(path => path.includes('date=2026-09-15'))).toBe(false)

    unmount()
  })
})
