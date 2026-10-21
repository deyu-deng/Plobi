import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'

import { AgentActionsMenu } from './agent-actions-menu'

// 左栏每个 Agent 行旁边的「三个点」（2026-10-21 用户反馈：这格还没有做出来）。
// 这份测试钉住三件事：① 那一格确实在、② 它只放**这个 Agent 自己**的事
// （打开 / 能力 / 归档——没有「换 profile」这类已退役的概念），③ 归档要用户
// 点头，且失败要出声。

const dissolveAgent = vi.fn(async () => ({ id: 'aura' }) as never)
const refreshConsoleAgents = vi.fn(async () => {})
const notify = vi.fn()
const notifyError = vi.fn()
const navigate = vi.fn()
const writeClipboardText = vi.fn(async (_text: string) => {})

vi.mock('@/app/console/api', () => ({
  dissolveAgent: (...args: unknown[]) => dissolveAgent(...(args as []))
}))

vi.mock('@/app/console/store/agents', () => ({
  refreshConsoleAgents: () => refreshConsoleAgents()
}))

vi.mock('@/components/ui/copy-button', () => ({
  writeClipboardText: (text: string) => writeClipboardText(text)
}))

vi.mock('@/store/notifications', () => ({
  notify: (input: unknown) => notify(input),
  notifyError: (error: unknown, fallback: string) => notifyError(error, fallback)
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate
}))

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { close: '关闭' },
      console: {
        agentMenuMore: '更多',
        agentMenuMoreFor: (name: string) => `${name} 的更多操作`,
        agentMenuOpen: '打开对话',
        agentMenuCopyId: '复制 ID',
        agentMenuCopied: 'Agent 的 ID 已复制。',
        agentMenuCopyFailed: 'ID 没能复制：',
        agentMenuArchive: '归档这个 Agent',
        agentMenuArchiveConfirmTitle: '归档这个 Agent？',
        agentMenuArchiveConfirmBody: '{name} 会从左栏消失。',
        agentMenuArchiveCancel: '取消',
        agentMenuArchiveConfirmAction: '归档',
        agentMenuArchived: 'Agent 已归档。',
        agentMenuArchiveFailed: '这个 Agent 没能归档：'
      }
    }
  })
}))

const agent: Agent = {
  category: 'projects',
  id: 'aura',
  name: 'Aura',
  profile: 'l2-aura',
  status: 'idle'
} as Agent

afterEach(() => {
  cleanup()
  dissolveAgent.mockClear()
  refreshConsoleAgents.mockClear()
  notify.mockClear()
  notifyError.mockClear()
  writeClipboardText.mockClear()
  navigate.mockClear()
})

describe('AgentActionsMenu', () => {
  it('has a "more" trigger next to the agent row', () => {
    render(<AgentActionsMenu agent={agent} onNavigate={() => {}} />)

    expect(screen.getByLabelText('Aura 的更多操作')).toBeTruthy()
  })

  it('offers only this agent’s own things — no profile switching', async () => {
    render(<AgentActionsMenu agent={agent} onNavigate={() => {}} />)

    fireEvent.pointerDown(screen.getByLabelText('Aura 的更多操作'), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })

    expect(await screen.findByRole('menuitem', { name: '打开对话' })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: '复制 ID' })).toBeTruthy()
    expect(screen.getByRole('menuitem', { name: '归档这个 Agent' })).toBeTruthy()
    // profile 那个概念已退役：菜单里不许再冒出它。
    expect(screen.queryByText(/profile/i)).toBeNull()
  })

  it('copies the agent id and opens its conversation', async () => {
    const onNavigate = vi.fn()
    render(<AgentActionsMenu agent={agent} onNavigate={onNavigate} />)

    fireEvent.pointerDown(screen.getByLabelText('Aura 的更多操作'), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    fireEvent.click(await screen.findByRole('menuitem', { name: '打开对话' }))
    expect(onNavigate).toHaveBeenCalledWith('aura')

    fireEvent.pointerDown(screen.getByLabelText('Aura 的更多操作'), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    fireEvent.click(await screen.findByRole('menuitem', { name: '复制 ID' }))
    expect(writeClipboardText).toHaveBeenCalledWith('aura')
  })

  it('asks before archiving, then archives and refreshes the roster', async () => {
    render(<AgentActionsMenu agent={agent} onNavigate={() => {}} />)

    fireEvent.pointerDown(screen.getByLabelText('Aura 的更多操作'), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    fireEvent.click(await screen.findByRole('menuitem', { name: '归档这个 Agent' }))

    // 点头之前不许动手。
    expect(await screen.findByText('归档这个 Agent？')).toBeTruthy()
    expect(dissolveAgent).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('归档'))

    await waitFor(() => expect(dissolveAgent).toHaveBeenCalledWith('aura'))
    await waitFor(() => expect(refreshConsoleAgents).toHaveBeenCalled())
    expect(notify).toHaveBeenCalled()
  })

  it('says so when archiving fails', async () => {
    dissolveAgent.mockRejectedValueOnce(new Error('boom'))
    render(<AgentActionsMenu agent={agent} onNavigate={() => {}} />)

    fireEvent.pointerDown(screen.getByLabelText('Aura 的更多操作'), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    fireEvent.click(await screen.findByRole('menuitem', { name: '归档这个 Agent' }))
    fireEvent.click(screen.getByText('归档'))

    await waitFor(() => expect(notifyError).toHaveBeenCalled())
  })
})
