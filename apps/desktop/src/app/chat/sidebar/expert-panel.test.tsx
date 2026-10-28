// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpertPanel } from './expert-panel'

const api = vi.fn()

const DATA = {
  ok: true,
  data: {
    experts: [
      { goal: '把三份资料折成一份汇总', id: 'kid-1', messages: 4, model: 'glm', parent_session_id: 'p1', started_at: Date.now() / 1000 - 30, status: 'running', tool_calls: 2 },
      { goal: '查一下这门课的作业', id: 'kid-2', messages: 6, model: 'glm', parent_session_id: 'p1', started_at: Date.now() / 1000 - 600, status: 'done', tool_calls: 5 }
    ],
    live: 1
  }
}

const LOG = {
  session_id: 'kid-1',
  messages: [
    { role: 'user', content: '把三份资料折成一份汇总' },
    { role: 'assistant', content: '我先读这三份资料。' },
    { role: 'tool', tool_name: 'read_file', content: '...' }
  ]
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

describe('ExpertPanel', () => {
  it('派出去的专家出现在名单里，正在干的打点，点开是它自己的工作日志', async () => {
    api.mockImplementation(({ path }: { path: string }) =>
      path.endsWith('/experts') ? Promise.resolve(DATA) : Promise.resolve(LOG)
    )
    render(<ExpertPanel agentId="Sindri" />)

    expect(await screen.findByText('running 1')).toBeTruthy()
    expect(screen.getByText('把三份资料折成一份汇总')).toBeTruthy()
    expect(screen.getByText('查一下这门课的作业')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /把三份资料折成一份汇总/ }))

    expect(await screen.findByText('我先读这三份资料。')).toBeTruthy()
    expect(screen.getByText('⚙ read_file')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/sessions/kid-1/messages' }))
  })

  it('每 3.5 秒重读一次名单：L2 刚派出去的那一行会自己冒出来', async () => {
    vi.useFakeTimers()
    try {
      api.mockImplementation(() => Promise.resolve(DATA))
      render(<ExpertPanel agentId="Sindri" />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      const first = api.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(3500) })

      expect(api.mock.calls.length).toBeGreaterThan(first)
    } finally {
      vi.useRealTimers()
    }
  })

  it('没派过就说一句，不画空壳', async () => {
    api.mockResolvedValue({ ok: true, data: { experts: [], reason: '名册里 Sindri 还没有会话' } })
    render(<ExpertPanel agentId="Sindri" />)

    await waitFor(() => expect(screen.getByText('名册里 Sindri 还没有会话')).toBeTruthy())
  })
})
