import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $agendaEvents } from '@/store/agenda'
import type { AgendaEvent } from '@/types/hermes'

const confirmAgendaEvent = vi.hoisted(() => vi.fn())
const dismissAgendaEvent = vi.hoisted(() => vi.fn())

vi.mock('@/hermes', () => ({
  confirmAgendaEvent: (...args: unknown[]) => confirmAgendaEvent(...args),
  dismissAgendaEvent: (...args: unknown[]) => dismissAgendaEvent(...args)
}))

import {
  confirmAgendaEventAction,
  createAgent,
  getAgentOverview,
  getAgentSubagentLog,
  getOutsourcedSessionTarget
} from './api'

function baseEvent(): AgendaEvent {
  const now = '2026-08-30T08:00:00Z'

  return {
    confirm_seq: 12,
    created_at: now,
    end_at: null,
    id: 'e1',
    kind: 'class',
    source: 'wechat',
    start_at: '2026-08-31T10:00:00',
    status: 'pending',
    title: '高数课改期',
    updated_at: now
  }
}

describe('confirmAgendaEventAction (U5-seg1 write-back)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    $agendaEvents.set([baseEvent()])
  })

  it('upserts the confirmed row into the shared agenda store', async () => {
    const confirmed = { ...baseEvent(), status: 'confirmed' as const }
    confirmAgendaEvent.mockResolvedValue(confirmed)

    await confirmAgendaEventAction('e1', 'confirm')

    expect(confirmAgendaEvent).toHaveBeenCalledWith('e1')
    expect($agendaEvents.get().find(e => e.id === 'e1')?.status).toBe('confirmed')
  })

  it('removes the row when the backend reports a deletion on dismiss', async () => {
    dismissAgendaEvent.mockResolvedValue({ deleted: true, id: 'e1', ok: true })

    await confirmAgendaEventAction('e1', 'dismiss')

    expect(dismissAgendaEvent).toHaveBeenCalledWith('e1')
    expect($agendaEvents.get().some(e => e.id === 'e1')).toBe(false)
  })

  it('upserts the returned row when dismiss answers with a row', async () => {
    const dismissed = { ...baseEvent(), status: 'cancelled' as const }
    dismissAgendaEvent.mockResolvedValue(dismissed)

    await confirmAgendaEventAction('e1', 'dismiss')

    expect($agendaEvents.get().find(e => e.id === 'e1')?.status).toBe('cancelled')
  })
})

// [WAIT-BACKEND] both are mocked: neither endpoint exists in the §5 contract
// yet. The tests pin the shape the UI depends on so swapping in the real
// request is a one-function change.
describe('createAgent (WP-L2-FE)', () => {
  it('POSTs /api/agents with the create request', async () => {
    const api = vi.fn().mockResolvedValue({
      data: { id: 'plobi-code', name: 'plobi-code', status: 'idle', todayCalls: 0 },
      ok: true
    })
    const previous = (window as { hermesDesktop?: unknown }).hermesDesktop

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })

    try {
      const created = await createAgent({ id: 'plobi-code', role: 'l2_project' })

      expect(api).toHaveBeenCalledWith({
        body: { id: 'plobi-code', role: 'l2_project' },
        method: 'POST',
        path: '/api/agents'
      })
      expect(created.id).toBe('plobi-code')
    } finally {
      if (previous) {
        Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: previous })
      } else {
        Reflect.deleteProperty(window, 'hermesDesktop')
      }
    }
  })
})

describe('S2 mock endpoints (U4)', () => {
  it('returns the mocked work log for a known sub-agent', async () => {
    const log = await getAgentSubagentLog('agenda-secretary', 'sub-extractor')

    expect(log.lines.length).toBeGreaterThan(0)
  })

  it('rejects an unknown sub-agent so the dialog can show its error state', async () => {
    await expect(getAgentSubagentLog('agenda-secretary', 'nope')).rejects.toThrow(/not found/)
  })

  it('returns a deep-link target for an outsourced conversation', async () => {
    const target = await getOutsourcedSessionTarget('cursor', 'cur 1')

    expect(target.url).toContain('cursor')
    expect(target.url).toContain('cur%201')
  })
})

// R-013 (裁定 20): the overview is the single source for the agent's bound
// folder. The live envelope carries `projectPath` through; the butler mock
// fallback has no binding, so the field stays absent (file tree must stay
// empty instead of inheriting the previous cwd).
describe('getAgentOverview projectPath (WP-R013-FE)', () => {
  function stubHermesDesktop(api: ReturnType<typeof vi.fn>): () => void {
    const previous = (window as { hermesDesktop?: unknown }).hermesDesktop

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })

    return () => {
      if (previous) {
        Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: previous })
      } else {
        Reflect.deleteProperty(window, 'hermesDesktop')
      }
    }
  }

  it('surfaces the backend-bound folder from the live envelope', async () => {
    const api = vi.fn().mockResolvedValue({
      data: {
        agent: { id: 'plobi-code', name: 'plobi-code', status: 'idle', todayCalls: 0 },
        projectPath: 'D:\\projects\\plobi',
        sessionId: 'sess-1',
        todayCostUsd: 0,
        todayTokens: 0
      },
      ok: true
    })
    const restore = stubHermesDesktop(api)

    try {
      const overview = await getAgentOverview('plobi-code')

      expect(overview.projectPath).toBe('D:\\projects\\plobi')
    } finally {
      restore()
    }
  })

  it('keeps the butler mock fallback free of a bound folder', async () => {
    const api = vi.fn().mockRejectedValue(new Error('offline'))
    const restore = stubHermesDesktop(api)

    try {
      const overview = await getAgentOverview('agenda-secretary')

      expect(overview.projectPath).toBeUndefined()
      expect(overview.agent.id).toBe('agenda-secretary')
    } finally {
      restore()
    }
  })
})
