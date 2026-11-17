import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setConsoleAgents } from '@/app/console/store/agents'
import type { Agent } from '@/app/console/types'
import { I18nProvider } from '@/i18n'
import { $agentHandoff, clearAgentHandoff } from '@/store/agent-handoff'

import { ChatSelectionActions, messageIdForRange } from './selection-actions'

const { ensureGatewayProfile, setQuoteTarget, notifyError } = vi.hoisted(() => ({
  ensureGatewayProfile: vi.fn(async (_profile: string) => undefined),
  setQuoteTarget: vi.fn(),
  notifyError: vi.fn()
}))

// 名册那一档的 API 层会拉真后端，这一档不需要它。
vi.mock('@/app/console/api', () => ({ fetchAgents: vi.fn(async () => []) }))
// 只换掉「切后端」那一步：这一档要验的是**顺序**（先换后端、再跳界面），不是后端本身。
vi.mock('@/store/scope', () => ({
  ensureGatewayProfile: (profile: string) => ensureGatewayProfile(profile)
}))
vi.mock('@/store/quote', async importOriginal => ({
  ...((await importOriginal()) as Record<string, unknown>),
  setQuoteTarget
}))
vi.mock('@/store/notifications', () => ({
  notifyError: (error: unknown, fallback: string) => notifyError(error, fallback)
}))

const TEXT = '这句话要发给别的智能体'

function agent(id: string, name: string): Agent {
  return { id, name, status: 'idle', todayCalls: 0 } as Agent
}

function selectNode(node: Node) {
  const range = document.createRange()

  range.selectNodeContents(node)

  const selection = window.getSelection()

  selection?.removeAllRanges()
  selection?.addRange(range)
}

function LocationProbe() {
  const location = useLocation()

  return <span data-testid="location">{location.pathname}</span>
}

function Harness() {
  const ref = useRef<HTMLDivElement | null>(null)

  return (
    <I18nProvider configClient={null} initialLocale="zh">
      <MemoryRouter initialEntries={['/']}>
        <div ref={ref}>
          <p data-message-id="m-1" data-selectable-text="true">
            {TEXT}
          </p>
        </div>
        <ChatSelectionActions containerRef={ref} />
        <LocationProbe />
      </MemoryRouter>
    </I18nProvider>
  )
}

function openToolbar() {
  selectNode(screen.getByText(TEXT))
  fireEvent.mouseUp(document)
}

describe('聊天里的选区浮窗（WP-SELECTION-TOOLBAR）', () => {
  beforeEach(() => {
    setConsoleAgents([agent('aura', '光环'), agent('nova', '新星')])
  })

  afterEach(() => {
    cleanup()
    clearAgentHandoff()
    ensureGatewayProfile.mockClear()
    setQuoteTarget.mockClear()
    notifyError.mockClear()
    window.getSelection()?.removeAllRanges()
  })

  it('从选区往上找得到那条消息的 id', () => {
    const holder = document.createElement('div')

    holder.setAttribute('data-message-id', 'm-9')

    const paragraph = document.createElement('p')

    paragraph.textContent = '选中的字'
    holder.appendChild(paragraph)
    document.body.appendChild(holder)

    const range = document.createRange()

    range.selectNodeContents(paragraph)

    expect(messageIdForRange(range)).toBe('m-9')

    holder.remove()
  })

  it('引用挂的是选中的那一段，不是整条', () => {
    render(<Harness />)
    openToolbar()

    fireEvent.click(screen.getByTestId('chat-selection-toolbar-quote'))

    expect(setQuoteTarget).toHaveBeenCalledWith({ id: 'm-1', text: TEXT })
  })

  it('「发给智能体」自带下拉，列的是名册里的那几个', () => {
    render(<Harness />)
    openToolbar()

    fireEvent.click(screen.getByTestId('chat-selection-toolbar-send-to-agent'))

    expect(screen.getByTestId('chat-selection-toolbar-menu-aura').textContent).toBe('光环')
    expect(screen.getByTestId('chat-selection-toolbar-menu-nova').textContent).toBe('新星')
  })

  it('选一个智能体 ⇒ 先换后端、再跳到它那儿、话记成待办', async () => {
    render(<Harness />)
    openToolbar()

    fireEvent.click(screen.getByTestId('chat-selection-toolbar-send-to-agent'))
    fireEvent.click(screen.getByTestId('chat-selection-toolbar-menu-aura'))

    await waitFor(() => expect(ensureGatewayProfile).toHaveBeenCalledWith('aura'))

    expect(screen.getByTestId('location').textContent).toBe('/agent/aura')
    expect($agentHandoff.get()).toEqual({ agentId: 'aura', targetKey: 'agent:aura', text: TEXT })
  })

  it('点不动的那几行（数据条目）不进下拉', () => {
    setConsoleAgents([agent('aura', '光环'), { ...agent('course', '课程'), navigable: false }])
    render(<Harness />)
    openToolbar()

    fireEvent.click(screen.getByTestId('chat-selection-toolbar-send-to-agent'))

    expect(screen.getByTestId('chat-selection-toolbar-menu-aura')).toBeTruthy()
    expect(screen.queryByTestId('chat-selection-toolbar-menu-course')).toBeNull()
  })

  it('后端切不过去 ⇒ 说一声，不记待办、不跳界面', async () => {
    ensureGatewayProfile.mockRejectedValueOnce(new Error('gateway down'))
    render(<Harness />)
    openToolbar()

    fireEvent.click(screen.getByTestId('chat-selection-toolbar-send-to-agent'))
    fireEvent.click(screen.getByTestId('chat-selection-toolbar-menu-aura'))

    await waitFor(() => expect(notifyError).toHaveBeenCalledTimes(1))

    // 没吭声的话，用户点了却像没点——这一族最招人烦的形状。
    expect(notifyError.mock.calls[0]?.[1]).toBeTruthy()
    expect($agentHandoff.get()).toBeNull()
    expect(screen.getByTestId('location').textContent).toBe('/')
  })
})
