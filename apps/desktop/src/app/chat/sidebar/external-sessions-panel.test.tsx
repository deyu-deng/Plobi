// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExternalSessionsPanel } from './external-sessions-panel'

const api = vi.fn()

const LIST = {
  ok: true,
  data: {
    projects: ['D:/Projects/Sindri'],
    reason: '',
    vendor: 'qoder',
    sessions: [{
      id: 'aaaaaaaa-1111',
      project: 'D:/Projects/Sindri',
      related: true,
      related_source: 'path',
      sendable: true,
      title: '先读 README，把标题写进回执文件',
      updated_at: '2026-10-27T10:00:00.000Z',
      vendor: 'qoder'
    }]
  }
}

const TRANSCRIPT = {
  ok: true,
  data: {
    id: 'aaaaaaaa-1111',
    project: 'D:/Projects/Sindri',
    vendor: 'qoder',
    messages: [
      { role: 'user', text: '先读 README' },
      { role: 'assistant', text: '读完了，标题是 Sindri' }
    ]
  }
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

function route(sendReply = '第二句回话') {
  api.mockImplementation(({ path }: { path: string }) => {
    if (path.endsWith('/external-sessions')) {
      return Promise.resolve(LIST)
    }

    if (path.endsWith('/send')) {
      return Promise.resolve({ ok: true, data: { reply: sendReply, credits: 1.2, cost_status: 'credits_only' } })
    }

    return Promise.resolve(TRANSCRIPT)
  })
}

describe('ExternalSessionsPanel', () => {
  it('按项目列出会话，点一条在 Plobi 里读 transcript（不跳外部软件）', async () => {
    route()
    render(<ExternalSessionsPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))
    fireEvent.click(await screen.findByRole('button', { name: /先读 README/ }))

    expect(await screen.findByText('读完了，标题是 Sindri')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: '/api/agents/Sindri/external-sessions'
    }))
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: '/api/agents/Sindri/external-sessions/qoder/aaaaaaaa-1111'
    }))
  })

  it('能在这个面板里说一句，回复接在 transcript 后面', async () => {
    route()
    render(<ExternalSessionsPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))
    fireEvent.click(await screen.findByRole('button', { name: /先读 README/ }))
    await screen.findByText('读完了，标题是 Sindri')

    fireEvent.change(screen.getByPlaceholderText('Say something to it…'), { target: { value: '标题再念一遍' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('第二句回话')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      body: { text: '标题再念一遍' },
      method: 'POST',
      path: '/api/agents/Sindri/external-sessions/qoder/aaaaaaaa-1111/send'
    }))
  })

  it('后端拒了就把那句话说出来，不甩 JSON', async () => {
    route()
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve(LIST)
      }

      if (path.endsWith('/send')) {
        return Promise.reject(new Error('400: {"ok":false,"error":"这次没有登进去"}'))
      }

      return Promise.resolve(TRANSCRIPT)
    })
    render(<ExternalSessionsPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))
    fireEvent.click(await screen.findByRole('button', { name: /先读 README/ }))
    await screen.findByText('读完了，标题是 Sindri')

    fireEvent.change(screen.getByPlaceholderText('Say something to it…'), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/这次没有登进去/)).toBeTruthy()
    expect(screen.queryByText(/\{"ok"/)).toBeNull()
  })

  it('空表说原因，不画空壳', async () => {
    api.mockResolvedValue({ ok: true, data: { projects: [], reason: '这一条没有绑定目录', sessions: [], vendor: 'qoder' } })
    render(<ExternalSessionsPanel agentId="Sindri" />)

    await waitFor(() => expect(screen.getByText('这一条没有绑定目录')).toBeTruthy())
  })
  it('L2 标过无关的不出现；还没判断的收在开关里', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'rel-1', title: '相关的一条', related: true },
          { ...LIST.data.sessions[0], id: 'no-1', title: '无关的一条', related: false },
          { ...LIST.data.sessions[0], id: 'un-1', title: '待判断的一条', related: null }
        ] } })
      }

      return Promise.resolve(TRANSCRIPT)
    })
    render(<ExternalSessionsPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))

    expect(await screen.findByText('相关的一条')).toBeTruthy()
    expect(screen.queryByText('无关的一条')).toBeNull()
    expect(screen.queryByText('待判断的一条')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Unjudged/ }))
    expect(await screen.findByText('待判断的一条')).toBeTruthy()
  })

  it('只读的那家不给输入框，明确说不能从这里说话', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'conv_1', sendable: false, title: 'Computor', vendor: 'marvis' }
        ] } })
      }

      return Promise.resolve({ ok: true, data: { id: 'conv_1', messages: [{ role: 'user', text: 'Computor' }], project: '', vendor: 'marvis' } })
    })
    render(<ExternalSessionsPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Marvis/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Computor/ }))

    expect(await screen.findByText('You cannot speak to this app from Plobi yet.')).toBeTruthy()
    expect(screen.queryByPlaceholderText('Say something to it…')).toBeNull()
  })

})
