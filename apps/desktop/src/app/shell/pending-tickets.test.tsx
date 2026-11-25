// @vitest-environment jsdom
import { useStore } from '@nanostores/react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $pendingInboxCount } from '@/store/pending-inbox'

import { PendingTicketsFace, usePendingInboxPolling } from './pending-tickets'

/**
 * **11-25 更新（裁定 239 第四刀）**：那一枚入口不再是一个各写各的壳
 * （`PendingTicketsButton` 删掉了——抬头那一列改成**从 slot 声明表派生**，
 * 一枚 chat-card slot ⇒ 一枚 `MonitorEntryButton`）。
 *
 * **轮询住在哪里**：角标那个数要**面板关着的时候**也准 ⇒ 轮询跟着那一格走
 * （`usePendingInboxPolling`）。这一档钉的还是同一件事：**两清单都拉了，
 * 收件箱里几件，角标上就是几**——这里把「角标那个数」直接读出来。
 */
function PendingBadge() {
  usePendingInboxPolling()
  const count = useStore($pendingInboxCount)

  return <span data-testid="pending-count">{count}</span>
}

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
  it('人已表态过的票不再列为待办（点了就消失，不再连点 13 次）', async () => {
    api.mockImplementation((req: { path?: string, method?: string }) => {
      if (req.path?.includes('/api/decisions')) {
        return Promise.resolve({
          ok: true,
          data: { decisions: [{ ticket_id: TICKET.id, kind: 'accept' }] }
        })
      }
      if (req.path?.includes('/api/tickets/escalations')) {
        return Promise.resolve({ ok: true, data: { tickets: [] } })
      }
      if (req.path?.includes('/api/lessons')) {
        return Promise.resolve({ ok: true, data: { lessons: [] } })
      }
      return Promise.resolve({ ok: true, data: { tickets: [TICKET] } })
    })
    render(<PendingTicketsFace />)

    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/decisions')
    })))
    await waitFor(() => expect(screen.queryByText('顺手把测试补一下')).toBeNull())
  })

  it('外部事件票与需人汇报都进收件箱，数量摆在图标角标上', async () => {
    mockBothLists([{ id: 'tkt_r1', goal: '改配色', leg: 'router', state: 'blocked', status: 'active', one_line: '「改配色」卡住了：缺一张图。要我继续，还是你来定？' }])
    render(<PendingBadge />)

    expect(await screen.findByText('2')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/tickets?status=observed&leg=trigger')
    }))
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/api/tickets/escalations')
    }))
  })

  it('一行放不下时仍截断，但全文不走原生 title（Electron 里会被截掉）', async () => {
    mockBothLists()
    render(<PendingTicketsFace />)

    const row = await screen.findByText('顺手把测试补一下')
    expect(row.closest('[title]')).toBeNull()
  })

  it('需人汇报票是主副两行：主行＝它说了什么，副行＝哪件事·谁·状态·多久前（裁定 236）', async () => {
    mockBothLists([{
      at_ms: Date.now() - 60_000,
      goal: '先回我一句现状',
      id: 'tkt_r2',
      leg: 'router',
      note: '装机版还是 v0.3.2',
      one_line: '「先回我一句现状」等你验收：装机版还是 v0.3.2。要我做下一步吗？',
      state: 'ready',
      status: 'active',
      who: 'nsep'
    }])
    render(<PendingTicketsFace />)

    // 主行是回执正文（它真正那句话），不是派活原话，也不是后端拼好的整句。
    expect(await screen.findByText('装机版还是 v0.3.2')).toBeTruthy()
    expect(screen.queryByText(/等你验收：装机版/)).toBeNull()

    // 副行四件：哪件事 · 谁 · 状态词 · 多久前。
    const sub = screen.getByText(/nsep/)
    expect(sub.textContent).toContain('先回我一句现状')
    expect(sub.textContent).toMatch(/等你验收|ready for review/)
    expect(sub.textContent).toMatch(/分钟|minute/)
  })

  it('接纳 / 拒绝各走一条三态 POST，之后重读清单', async () => {
    mockBothLists()
    render(<PendingTicketsFace />)

    expect(await screen.findByText('顺手把测试补一下')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /接纳|Accept/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      body: { comment: '', kind: 'accept' },
      path: `/api/tickets/${TICKET.id}/decision`
    })))

    fireEvent.click(screen.getByRole('button', { name: /拒绝|Reject/ }))
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

    fireEvent.click(screen.getByRole('button', { name: /评论|Comment/ }))
    const box = screen.getByPlaceholderText(/说清楚为什么|Explain why/)
    fireEvent.change(box, { target: { value: '以后配色这类你定' } })
    fireEvent.click(screen.getByRole('button', { name: /提交评论|Send comment/ }))
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

    fireEvent.click(screen.getByRole('button', { name: /批准授权|Approve grant/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/lessons/${DRAFT.id}/approve`
    })))

    fireEvent.click(screen.getByRole('button', { name: /驳回授权|Reject grant/ }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({
      method: 'POST',
      path: `/api/lessons/${DRAFT.id}/reject`
    })))

    const ticketPosts = api.mock.calls.filter(
      call => /\/tickets\/[^/]+\/decision$/.test(String(call[0]?.path ?? ''))
    )

    expect(ticketPosts).toHaveLength(0), '授权动作不许走票的三态端点'
  })

  it('已生效授权只给撤回图标，走 lesson revoke', async () => {
    mockBothLists([], [LIVE], [])
    render(<PendingTicketsFace />)
    expect(await screen.findByText('配色这类你自己定')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /批准授权|Approve grant/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /撤回授权|Revoke grant/ }))
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
    expect(await screen.findByText(/读不到收件箱|读不到待放行清单|Could not load/)).toBeTruthy()
  })
})
