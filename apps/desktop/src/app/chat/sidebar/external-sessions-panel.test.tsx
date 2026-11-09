// @vitest-environment jsdom
/**
 * 外部会话那一族的行为：读 / 说 / 改名 / 归档 / 删除 / 待判断。
 *
 * 11-01 起它们不再是一整格独立面板，而是并进「外包」那一格（`AgentOffloadPanel`）的同一张
 * 按软件分组的表里（用户：「Agent 自己外包的和我在别的 AI 软件里面的对话其实是平等的」），
 * 所以这一档渲染的是那个面板。
 *
 * 同一次改形里，点一行**不再弹窗**：它把这条会话交给 `$openConversation`，主区用对话卡片
 * （`ConversationPane`）显示。于是「读到什么、能不能说话、改名/归档/删除」这些断言搬到
 * `app/console/chat/conversation-pane.test.tsx`——这一档只负责「列表对不对、点下去交出去的是
 * 不是那一条」。
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $openConversation, closeConversation } from '@/store/conversation-view'

import { AgentOffloadPanel } from './agent-offload-panel'

const api = vi.fn()
const getApi = () => api

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

beforeEach(() => {
  api.mockReset()
  closeConversation()
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  closeConversation()
  vi.clearAllMocks()
})

/** 只数「外部会话」这一路的读：这一格现在同时读台账与专家名单，混着数会多算。 */
function sessionReads(): number {
  return api.mock.calls.filter(([arg]) => String((arg as { path?: string })?.path ?? '').endsWith('/external-sessions'))
    .length
}

describe('外部会话（并进外包那一格）', () => {
  it('标题旁只留一个数；待判断的收在眼睛图标里', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'rel-1', related: true },
          { ...LIST.data.sessions[0], id: 'un-1', related: null }
        ] } })
      }

      return Promise.resolve({ ok: true, data: {} })
    })
    render(<AgentOffloadPanel agentId="Sindri" />)

    expect(await screen.findByText('Outsourced')).toBeTruthy()
    expect(screen.getByRole('button', { name: /judged/i })).toBeTruthy()
  })

  it('点一条不是弹窗：把这条会话交给主区那张对话卡片', async () => {
    api.mockResolvedValue(LIST)
    render(<AgentOffloadPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))
    fireEvent.click(await screen.findByRole('button', { name: /先读 README/ }))

    expect($openConversation.get()).toEqual({
      agentId: 'Sindri',
      kind: 'external',
      sendable: true,
      sessionId: 'aaaaaaaa-1111',
      title: '先读 README，把标题写进回执文件',
      // 这条会话最后一句话的时间也要带过去：抬头要写「什么时候的」。
      updatedAt: '2026-10-27T10:00:00.000Z',
      vendor: 'qoder'
    })
    // 左栏自己不再读正文：正文归主区，左栏只管挑。
    expect(api).toHaveBeenCalledWith(expect.objectContaining({
      path: '/api/agents/Sindri/external-sessions'
    }))
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({
      path: expect.stringContaining('/external-sessions/qoder/aaaaaaaa-1111')
    }))
  })

  it('OpenCode 的会话按同形出现：自己一组，点进去交出去的是那一条', async () => {
    api.mockResolvedValue({ ok: true, data: { projects: [], reason: '', sessions: [
      { ...LIST.data.sessions[0], id: 'ses_abc123', vendor: 'opencode', title: '验收一下', sendable: true }
    ] } })
    render(<AgentOffloadPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: /OpenCode/ }))
    fireEvent.click(await screen.findByRole('button', { name: /验收一下/ }))

    // 屏上与别家同形：分组行是**品牌方块**，不是那枚中性「机器人」。
    expect(screen.getByTestId('vendor-brand-block-opencode')).toBeTruthy()
    expect(screen.queryByTestId('offload-vendor-fallback-opencode')).toBeNull()

    expect($openConversation.get()).toEqual({
      agentId: 'Sindri',
      kind: 'external',
      sendable: true,
      sessionId: 'ses_abc123',
      title: '验收一下',
      updatedAt: '2026-10-27T10:00:00.000Z',
      vendor: 'opencode'
    })
  })

  it('那家软件不支持从这里说话时，交出去的 `sendable` 是 false（主区据此收起输入框）', async () => {
    api.mockResolvedValue({ ok: true, data: { projects: [], reason: '', sessions: [
      { ...LIST.data.sessions[0], id: 'conv_1', sendable: false, title: 'Computor', vendor: 'marvis' }
    ] } })
    render(<AgentOffloadPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: /Marvis/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Computor/ }))

    expect($openConversation.get()).toEqual({
      agentId: 'Sindri',
      kind: 'external',
      sendable: false,
      sessionId: 'conv_1',
      title: 'Computor',
      updatedAt: '2026-10-27T10:00:00.000Z',
      vendor: 'marvis'
    })
  })

  it('开着这一格时每 5 秒重读：L2 在聊天里标完，左栏自己收窄', async () => {
    vi.useFakeTimers()

    try {
      getApi().mockResolvedValue(LIST)
      render(<AgentOffloadPanel agentId="Sindri" />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
      const first = sessionReads()
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })

      // 这一格现在同时读两路（台账 + 外部会话），所以只数会话这一路。
      expect(sessionReads()).toBe(first + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('空表不出现任何一行，屏上不放解释句', async () => {
    api.mockResolvedValue({ ok: true, data: { projects: [], reason: '这一条没有绑定目录', sessions: [], vendor: 'qoder' } })
    render(<AgentOffloadPanel agentId="Sindri" />)

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/agents/Sindri/offloads' }))
    )
    await act(async () => { await Promise.resolve() })

    expect(screen.queryByText('这一条没有绑定目录')).toBeNull()
    expect(screen.queryByRole('button', { name: /Qoder/ })).toBeNull()
  })

  it('L2 标过无关的不出现；还没判断的默认看得见（整行压暗一档），眼睛能收起来', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'rel-1', title: '相关的一条', related: true },
          { ...LIST.data.sessions[0], id: 'no-1', title: '无关的一条', related: false },
          { ...LIST.data.sessions[0], id: 'un-1', title: '待判断的一条', related: null }
        ] } })
      }

      return Promise.resolve({ ok: true, data: {} })
    })
    render(<AgentOffloadPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))

    expect(await screen.findByText('相关的一条')).toBeTruthy()
    expect(screen.queryByText('无关的一条')).toBeNull()

    // 11-09：待判断的那批**默认展开**——藏在一枚裸数字后面等于没有。
    // 状态由形状表达：整行压暗一档，不写「待判断」三个字上屏。
    const pending = await screen.findByText('待判断的一条')

    expect(pending.closest('.group\\/row')?.className).toContain('opacity-70')

    // 那枚眼睛还在，收起来就收起来。
    fireEvent.click(screen.getByRole('button', { name: /judged/i }))
    expect(screen.queryByText('待判断的一条')).toBeNull()
  })

  it('会话行只留标题 ＋ 时间；会话号与工作目录进悬停', async () => {
    api.mockResolvedValue(LIST)
    render(<AgentOffloadPanel agentId="Sindri" />)
    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))

    const row = await screen.findByRole('button', { name: /先读 README/ })

    // 机器细节进悬停：行面上一件也不多。
    expect(row.getAttribute('title')).toContain('aaaaaaaa-1111')
    expect(row.getAttribute('title')).toContain('D:/Projects/Sindri')
    expect(row.textContent).toMatch(/\d{2}:\d{2}|\d{2}-\d{2}/)
  })

  it('归档的收在「已归档」里，不在主列表', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'a1', title: '活着的一条', related: true },
          { ...LIST.data.sessions[0], id: 'a2', title: '归档的一条', related: true, archived: true }
        ] } })
      }

      return Promise.resolve({ ok: true, data: {} })
    })
    render(<AgentOffloadPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: /Qoder/ }))
    expect(await screen.findByText('活着的一条')).toBeTruthy()
    expect(screen.queryByText('归档的一条')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Archived' }))
    expect(await screen.findByText('归档的一条')).toBeTruthy()
  })

  it('归档那条也能点开（收回来的东西不该变成死的）', async () => {
    api.mockImplementation(({ path }: { path: string }) => {
      if (path.endsWith('/external-sessions')) {
        return Promise.resolve({ ok: true, data: { projects: [], reason: '', sessions: [
          { ...LIST.data.sessions[0], id: 'a2', related: true, archived: true, title: '归档的一条' }
        ] } })
      }

      return Promise.resolve({ ok: true, data: {} })
    })
    render(<AgentOffloadPanel agentId="Sindri" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Archived' }))
    fireEvent.click(await screen.findByRole('button', { name: /归档的一条/ }))

    expect($openConversation.get()).toEqual(expect.objectContaining({ kind: 'external', sessionId: 'a2' }))
  })
})
