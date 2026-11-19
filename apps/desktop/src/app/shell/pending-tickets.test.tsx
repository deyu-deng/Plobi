// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PendingTicketsButton, PendingTicketsFace } from './pending-tickets'

const api = vi.fn()

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

function mockBothLists(reports: unknown[] = [], lessons: unknown[] = [], drafts: unknown[] = []) {
  api.mockImplementation((req: { path?: string, method?: string }) => {
    if (req.method === 'POST') {
      return Promise.resolve({ ok: true })
    }

    if (req.path?.includes('/api/tickets/escalations')) {
      return Promise.resolve({ ok: true, data: { tickets: reports } })
    }

    if (req.path?.includes('/api/lessons?status=proposed')) {
      return Promise.resolve({ ok: true, data: { lessons: drafts } })
    }

    if (req.path?.includes('/api/lessons')) {
      return Promise.resolve({ ok: true, data: { lessons } })
    }

    return Promise.resolve({ ok: true, data: { tickets: [TICKET] } })
  })
}

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

const DRAFT = {
  id: 'les_draft1',
  status: 'proposed',
  scope: 'router:general',
  rule: '配色这类你自己定',
  question: '',
  user_words: '以后配色这类你定',
  evidence: ''
}

const LIVE = { ...DRAFT, id: 'les_live1', status: 'approved' }

describe('待放行那一族', () => {
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
    render(<PendingTicketsFace />)

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
    render(<PendingTicketsFace />)
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

  it('授权草稿用图标批准/驳回，走 lesson 端点而非票端点', async () => {
    mockBothLists([], [], [DRAFT])
    render(<PendingTicketsFace />)
    expect(await screen.findByText('配色这类你自己定')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '批准授权' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/lessons/${DRAFT.id}/approve`
    })))

    fireEvent.click(screen.getByRole('button', { name: '驳回授权' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/lessons/${DRAFT.id}/reject`
    })))

    const ticketPosts = api.mock.calls.filter(
      call => String(call[0]?.path ?? '').includes('/decision')
    )

    expect(ticketPosts).toHaveLength(0), '授权动作不许走票的三态端点'
  })

  it('已生效授权只给撤回图标，走 lesson revoke', async () => {
    mockBothLists([], [LIVE], [])
    render(<PendingTicketsFace />)
    expect(await screen.findByText('配色这类你自己定')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准授权' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '撤回授权' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/lessons/${LIVE.id}/revoke`
    })))
  })

  it('读不到清单就说读不到，不假称一条都没有', async () => {
    api.mockImplementation((req: { method?: string }) => {
      if (req.method === 'POST') {
        return Promise.resolve({ ok: true })
      }

      return Promise.resolve({ ok: false, error: 'db locked' })
    })
    render(<PendingTicketsFace />)
    expect(await screen.findByText(/读不到待放行清单|Could not load/)).toBeTruthy()
  })
})
