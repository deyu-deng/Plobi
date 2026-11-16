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

function mockBothLists(reports: unknown[] = []) {
  api.mockImplementation((req: { path?: string, method?: string }) => {
    if (req.method === 'POST') {
      return Promise.resolve({ ok: true })
    }
    if (req.path?.includes('/api/tickets/escalations')) {
      return Promise.resolve({ ok: true, data: { tickets: reports } })
    }
    return Promise.resolve({ ok: true, data: { tickets: [TICKET] } })
  })
}

describe('PendingTicketsButton', () => {
  it('外部事件票与需人汇报都进收件箱，数量摆在图标角标上', async () => {
    mockBothLists([{ id: 'tkt_r1', goal: '改配色', leg: 'router', state: 'blocked', status: 'active', one_line: '「改配色」卡住了：缺一张图。要我继续，还是你来定？' }])
    render(<PendingTicketsButton />)

    expect(await screen.findByText('2')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/tickets?status=observed&leg=trigger')
    }))
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/tickets/escalations')
    }))
  })

  it('接纳 / 拒绝各走一条三态 POST，之后重读清单', async () => {
    mockBothLists()
    render(<PendingTicketsButton />)

    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    expect(await screen.findByText('顺手把测试补一下')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '接纳' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      body: { comment: '', kind: 'accept' },
      path: `/api/tickets/${TICKET.id}/decision`
    })))

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      body: { comment: '', kind: 'reject' },
      path: `/api/tickets/${TICKET.id}/decision`
    })))
  })

  it('评论带原话走同一条 POST，空评论不提交', async () => {
    mockBothLists()
    render(<PendingTicketsButton />)
    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    expect(await screen.findByText('顺手把测试补一下')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '评论' }))
    const box = screen.getByPlaceholderText('说清楚为什么，让 Agent 理解后再记下来')
    fireEvent.change(box, { target: { value: '以后配色这类你定' } })
    fireEvent.click(screen.getByRole('button', { name: '提交评论' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      body: { comment: '以后配色这类你定', kind: 'comment' },
      path: `/api/tickets/${TICKET.id}/decision`
    })))
  })

  it('读不到清单就说读不到，不假称一条都没有', async () => {
    api.mockImplementation((req: { method?: string }) => {
      if (req.method === 'POST') {
        return Promise.resolve({ ok: true })
      }
      return Promise.resolve({ ok: false, error: 'db locked' })
    })
    render(<PendingTicketsButton />)
    fireEvent.click(screen.getByRole('button', { name: /待放行|Pending/ }))
    expect(await screen.findByText(/读不到待放行清单|Could not load/)).toBeTruthy()
  })
})
