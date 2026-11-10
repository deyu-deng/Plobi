// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PendingTicketsButton } from './pending-tickets'

const api = vi.fn()

// Radix Popover 在 jsdom 里量尺寸要 ResizeObserver；这里只给空实现，
// 不测布局，只测数据路径与动作。
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
})

const TICKET = {
  created_at: 0,
  goal: 'github/pull_request',
  id: 'tkt_a1b2c3',
  leg: 'trigger',
  parent_agent: 'l1',
  policy_version: '2026-11-08.1',
  refs: {
    event_type: 'pull_request',
    prompt: '顺手把测试补一下',
    prompt_excerpt: '顺手把测试补一下',
    route: 'github'
  },
  revoke_reason: '',
  scope: { allowed_capabilities: [] },
  status: 'observed',
  triggered_by: 'webhook:github',
  updated_at: 0
}

beforeEach(() => {
  api.mockReset()
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('PendingTicketsButton', () => {
  it('只读待放行的外部事件票，数量摆在图标角标上', async () => {
    api.mockResolvedValue({ ok: true, data: { tickets: [TICKET] } })
    render(<PendingTicketsButton />)

    expect(await screen.findByText('1')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/tickets?status=observed&leg=trigger')
    }))
  })

  it('放行 / 忽略各走一条 POST，之后重读清单', async () => {
    api.mockImplementation((req: { method?: string }) => {
      if (req.method === 'POST') {
        return Promise.resolve({ ok: true, data: { ticket: TICKET } })
      }

      return Promise.resolve({ ok: true, data: { tickets: [TICKET] } })
    })
    render(<PendingTicketsButton />)

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    expect(await screen.findByText('顺手把测试补一下')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /放行|Approve/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/tickets/${TICKET.id}/activate`
    })))

    fireEvent.click(screen.getByRole('button', { name: /忽略|Ignore/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/tickets/${TICKET.id}/revoke`
    })))
  })

  it('读不到清单就说读不到，不假称一条都没有', async () => {
    api.mockResolvedValue({ ok: false, error: 'db locked' })
    render(<PendingTicketsButton />)
    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    expect(await screen.findByText(/读不到待放行清单|Could not load/)).toBeTruthy()
  })
})
