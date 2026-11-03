// @vitest-environment jsdom
/**
 * 主区那张「外面的对话」卡片（用户 11-01：不要弹窗，直接复用对话卡片）。
 *
 * 它复用与正常对话同一条管线（`Thread` + 分支消息仓库 + 增量外部 store），区别只在数据源：
 * 外部软件那条会话，或者一个临时专家自己的工作日志。所以这一档把 `Thread` 换成一个
 * 只把消息正文摊出来的替身——要测的是「取数对不对、能不能说话、抬头那几个动作」，
 * 不是气泡长什么样（气泡自己有一档）。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $openConversation, type OpenConversation } from '@/store/conversation-view'

import { ConversationPane } from './conversation-pane'

vi.mock('@/components/assistant-ui/thread', async () => {
  const { useAuiState } = await import('@assistant-ui/react')

  return {
    Thread: () => {
      const messages = useAuiState(state => state.thread.messages)

      return (
        <div data-testid="thread">
          {messages.map(message => (
            <div data-testid="thread-line" key={message.id}>
              {message.content.map(part => (part.type === 'text' ? part.text : '')).join('')}
            </div>
          ))}
        </div>
      )
    }
  }
})

const api = vi.fn()

const EXTERNAL: OpenConversation = {
  agentId: 'Sindri',
  kind: 'external',
  sendable: true,
  sessionId: 's-1',
  title: '跟它说一句',
  vendor: 'qoder'
}

const EXPERT: OpenConversation = {
  agentId: 'Sindri',
  kind: 'expert',
  sessionId: 'kid-1',
  title: '整理三份资料'
}

const TRANSCRIPT = {
  data: {
    id: 's-1',
    messages: [
      { role: 'user', text: '先读 README' },
      { role: 'assistant', text: '读完了，标题是 Sindri' }
    ],
    project: 'D:/Projects/Sindri',
    vendor: 'qoder'
  },
  ok: true
}

const LOG = {
  messages: [
    { content: '整理三份资料', role: 'user' },
    { content: '我先读这三份资料。', role: 'assistant' },
    { content: '...', role: 'tool', tool_name: 'read_file' }
  ],
  session_id: 'kid-1'
}

beforeEach(() => {
  api.mockReset()
  $openConversation.set(EXTERNAL)
  Object.defineProperty(window, 'plobiDesktop', {
    configurable: true,
    value: { ...(window.plobiDesktop || {}), api }
  })
})

afterEach(() => {
  cleanup()
  $openConversation.set(null)
  vi.clearAllMocks()
})

function renderPane(conversation: OpenConversation = EXTERNAL, showHeader = true) {
  return render(<ConversationPane conversation={conversation} showHeader={showHeader} />)
}

/** Radix 的菜单是 `pointerdown` 开的，`click` 打不开（和 agent-actions-menu 那档同一个写法）。 */
function openActionsMenu() {
  fireEvent.pointerDown(screen.getByRole('button', { name: 'Actions for this outsourced conversation' }), {
    button: 0,
    ctrlKey: false,
    pointerType: 'mouse'
  })
}

describe('对话卡片：外部软件那条会话', () => {
  it('读那条会话的正文，逐句摆在卡片里（不是弹窗）', async () => {
    api.mockResolvedValue(TRANSCRIPT)
    renderPane()

    expect(await screen.findByText('读完了，标题是 Sindri')).toBeTruthy()
    expect(screen.getByText('先读 README')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({ path: '/api/agents/Sindri/external-sessions/qoder/s-1' })
    )
  })

  it('读不出来时把后端那句话放出来，不甩 JSON 给用户', async () => {
    api.mockRejectedValue(new Error('400: {"ok":false,"error":"这家软件今天没登进去"}'))
    renderPane()

    expect(await screen.findByText(/这家软件今天没登进去/)).toBeTruthy()
    expect(screen.queryByText(/\{"ok"/)).toBeNull()
  })

  it('能从这里说话的那家：发一句，回复接在正文后面', async () => {
    api.mockImplementation(({ path }: { path: string }) =>
      path.endsWith('/send')
        ? Promise.resolve({ data: { credits: 1.2, cost_status: 'credits_only', reply: '第二句回话' }, ok: true })
        : Promise.resolve(TRANSCRIPT)
    )
    renderPane()
    await screen.findByText('读完了，标题是 Sindri')

    fireEvent.change(screen.getByPlaceholderText('Say something to it…'), { target: { value: '标题再念一遍' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('第二句回话')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        body: { text: '标题再念一遍' },
        method: 'POST',
        path: '/api/agents/Sindri/external-sessions/qoder/s-1/send'
      })
    )
  })

  it('发不出去时当场写明，不留「点了没反应」', async () => {
    api.mockImplementation(({ path }: { path: string }) =>
      path.endsWith('/send')
        ? Promise.reject(new Error('400: {"ok":false,"error":"这次没有登进去"}'))
        : Promise.resolve(TRANSCRIPT)
    )
    renderPane()
    await screen.findByText('读完了，标题是 Sindri')

    fireEvent.change(screen.getByPlaceholderText('Say something to it…'), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText(/这次没有登进去/)).toBeTruthy()
  })

  it('只读的那家不给输入框', async () => {
    api.mockResolvedValue(TRANSCRIPT)
    renderPane({ ...EXTERNAL, sendable: false })

    await screen.findByText('读完了，标题是 Sindri')

    expect(screen.queryByPlaceholderText('Say something to it…')).toBeNull()
  })
})

describe('对话卡片：一个专家那张嘴', () => {
  it('读它自己的工作日志；第一条任务书已经在抬头里，不在正文重复', async () => {
    api.mockResolvedValue(LOG)
    renderPane(EXPERT)

    expect(await screen.findByText('我先读这三份资料。')).toBeTruthy()
    expect(screen.getByText('⚙ read_file')).toBeTruthy()
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/sessions/kid-1/messages' }))
    // 抬头写着的同一句话不再占正文一块。
    expect(screen.getAllByText('整理三份资料')).toHaveLength(1)
  })

  it('专家只读：没有输入框', async () => {
    api.mockResolvedValue(LOG)
    renderPane(EXPERT)

    await screen.findByText('我先读这三份资料。')

    expect(screen.queryByPlaceholderText('Say something to it…')).toBeNull()
  })
})

describe('对话卡片：抬头', () => {
  it('返回就退出这张卡片，主区回到正常对话', async () => {
    api.mockResolvedValue(TRANSCRIPT)
    renderPane()
    await screen.findByText('读完了，标题是 Sindri')

    fireEvent.click(screen.getByRole('button', { name: 'Back to conversation' }))

    expect($openConversation.get()).toBeNull()
  })

  it('卡片抬头可以关掉（外面已经画了标题栏时）', async () => {
    api.mockResolvedValue(TRANSCRIPT)
    renderPane(EXTERNAL, false)

    await screen.findByText('读完了，标题是 Sindri')

    expect(screen.queryByRole('button', { name: 'Back to conversation' })).toBeNull()
  })

  it('改名走 PATCH：只改我们这一侧', async () => {
    api.mockImplementation(({ method }: { method?: string }) =>
      method === 'PATCH' ? Promise.resolve({ data: {}, ok: true }) : Promise.resolve(TRANSCRIPT)
    )
    renderPane()
    await screen.findByText('读完了，标题是 Sindri')

    openActionsMenu()
    fireEvent.click(await screen.findByRole('menuitem', { name: /Rename/ }))

    fireEvent.change(screen.getByDisplayValue('跟它说一句'), { target: { value: '新名字' } })
    fireEvent.keyDown(screen.getByDisplayValue('新名字'), { key: 'Enter' })

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { title: '新名字' },
          method: 'PATCH',
          path: '/api/agents/Sindri/external-sessions/qoder/s-1'
        })
      )
    )
  })

  it('删除先要确认：第一次只是把菜单项变成「Confirm?」', async () => {
    api.mockImplementation(({ method }: { method?: string }) =>
      method === 'DELETE' ? Promise.resolve({ data: {}, ok: true }) : Promise.resolve(TRANSCRIPT)
    )
    renderPane()
    await screen.findByText('读完了，标题是 Sindri')

    const trigger = screen.getByRole('button', { name: 'Actions for this outsourced conversation' })

    openActionsMenu()
    fireEvent.click(await screen.findByRole('menuitem', { name: /Delete/ }))

    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ method: 'DELETE' }))

    // 菜单选完就关了，再打开一次才看得到「Confirm?」。
    expect(trigger).toBeTruthy()
    openActionsMenu()
    fireEvent.click(await screen.findByRole('menuitem', { name: /Confirm\?/ }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          method: 'DELETE',
          path: '/api/agents/Sindri/external-sessions/qoder/s-1'
        })
      )
    )
    expect($openConversation.get()).toBeNull()
  })
})
