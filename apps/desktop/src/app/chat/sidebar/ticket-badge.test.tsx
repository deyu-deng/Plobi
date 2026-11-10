// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TicketBadge } from './ticket-badge'

const api = vi.fn()

// Radix Popover 在 jsdom 里量尺寸要 ResizeObserver；这里只给空实现，
// 不测布局，只测数据路径与动作。
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
})

const DETAIL = {
  created_at: 0,
  goal: '整理纪要',
  id: 'tkt_a1b2c3',
  leg: 'expert',
  parent_agent: 'agenda',
  policy_version: '2026-11-08.1',
  refs: {},
  revoke_reason: '',
  scope: { allowed_capabilities: ['delegate'] },
  status: 'active',
  triggered_by: 'delegate_task',
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

describe('TicketBadge', () => {
  it('没有票号就不占位', () => {
    const { container } = render(<TicketBadge />)
    expect(container.firstChild).toBeNull()
  })

  it('点开看固化授权，active 票可以撤销', async () => {
    api.mockImplementation((req: { method?: string }) => {
      if (req.method === 'POST') {
        return Promise.resolve({ ok: true, data: {} })
      }

      return Promise.resolve({ ok: true, data: { ticket: DETAIL } })
    })
    render(<TicketBadge status="active" ticketId="tkt_a1b2c3" />)

    expect(screen.getByText('b2c3')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'tkt_a1b2c3' }))

    expect(await screen.findByText('2026-11-08.1')).toBeTruthy()
    expect(screen.getByText('delegate')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: '/api/tickets/tkt_a1b2c3'
    }))

    fireEvent.click(screen.getByRole('button', { name: /撤销|Revoke/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: '/api/tickets/tkt_a1b2c3/revoke'
    })))
  })

  it('终态票不给撤销按钮', async () => {
    api.mockResolvedValue({
      ok: true,
      data: { ticket: { ...DETAIL, status: 'done' } }
    })
    render(<TicketBadge status="done" ticketId="tkt_a1b2c3" />)
    fireEvent.click(screen.getByRole('button', { name: 'tkt_a1b2c3' }))

    expect(await screen.findByText('2026-11-08.1')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /撤销|Revoke/ })).toBeNull()
  })
})
