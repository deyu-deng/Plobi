import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/assistant-ui/thread', () => ({ Thread: () => <div data-testid="thread" /> }))

import { setGatewayState } from '@/store/session'

import { ChatSurface } from './chat-surface'
import { agentScope, L1_SCOPE } from './scope'

// The full ChatBar's ComposerStatusStack calls useNavigate(), so the surface
// must render inside a Router.
const renderSurface = (node: ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>)

async function collectTsx(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true })

  const files = await Promise.all(
    entries.map(async entry => {
      const full = join(dir, entry.name)

      if (entry.isDirectory()) {
        return collectTsx(full)
      }

      // Implementation sources only — test files are excluded so the guard
      // cannot count itself.
      return entry.name.endsWith('.tsx') && !entry.name.endsWith('.test.tsx') ? [full] : []
    })
  )

  return files.flat()
}

describe('ChatSurface (spec §3.6: one conversation component)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setGatewayState('open')
  })

  afterEach(() => {
    cleanup()
    setGatewayState('idle')
  })

  it('renders one transcript + composer for the L1 scope', () => {
    renderSurface(<ChatSurface scope={L1_SCOPE} />)

    expect(screen.getByTestId('thread')).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeTruthy()
  })

  it('renders the SAME surface for an L2 scope, only the copy differs', () => {
    renderSurface(<ChatSurface scope={agentScope('x')} />)

    expect(screen.getByTestId('thread')).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeTruthy()
  })

  it('forwards a real submit through the injected submit prop (L1)', async () => {
    const submit = vi.fn().mockResolvedValue(true)

    renderSurface(<ChatSurface scope={L1_SCOPE} submit={submit} />)

    const editor = screen.getByRole('textbox', { name: 'Message' })
    editor.textContent = 'hi'
    fireEvent.keyDown(editor, { key: 'Enter' })

    await waitFor(() => expect(submit).toHaveBeenCalled())
    expect(submit.mock.calls[0][0]).toBe('hi')
  })

  it('does not remount the transcript when scope or model menu changes', () => {
    const { rerender } = render(
      <MemoryRouter>
        <ChatSurface modelMenuContent={<span>m1</span>} scope={L1_SCOPE} />
      </MemoryRouter>
    )

    const thread = screen.getByTestId('thread')

    rerender(
      <MemoryRouter>
        <ChatSurface modelMenuContent={<span>m2</span>} scope={agentScope('agenda')} />
      </MemoryRouter>
    )

    expect(screen.getByTestId('thread')).toBe(thread)
    expect(screen.getByTestId('thread').closest('[data-chat-scope]')?.getAttribute('data-chat-scope')).toBe(
      'agent:agenda'
    )
  })

  it('exposes an accessible Send control once the L2 composer has text', async () => {
    renderSurface(<ChatSurface scope={agentScope('simulation')} />)

    const editor = screen.getByRole('textbox', { name: 'Message' })
    editor.textContent = 'hello'
    fireEvent.input(editor)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy())
  })

  it('forwards a real submit through the injected submit prop (L2 workbench)', async () => {
    const submit = vi.fn().mockResolvedValue(true)

    renderSurface(<ChatSurface scope={agentScope('x')} submit={submit} />)

    const editor = screen.getByRole('textbox', { name: 'Message' })
    editor.textContent = 'hi'
    fireEvent.keyDown(editor, { key: 'Enter' })

    await waitFor(() => expect(submit).toHaveBeenCalled())
    expect(submit.mock.calls[0][0]).toBe('hi')
  })
})

// Acceptance item added by spec §3.6: grep the console domain and assert there
// is exactly ONE chat rendering implementation. Both centers must be
// instantiations of `ChatSurface`; no other console file may own a runtime,
// transcript, or composer.
describe('spec §3.6 single-implementation guard', () => {
  // vitest jsdom reports `import.meta.url` as http, so anchor on cwd
  // (the desktop app root) instead of the module URL.
  const consoleDir = join(process.cwd(), 'src', 'app', 'console')

  it('has exactly one console module owning the chat runtime', async () => {
    const files = await collectTsx(consoleDir)
    const owners = []

    for (const file of files) {
      const source = await readFile(file, 'utf8')

      if (source.split('\n').some(line => line.includes('useIncrementalExternalStoreRuntime') && line.includes('import'))) {
        owners.push(file)
      }
    }

    expect(owners).toHaveLength(1)
    expect(owners[0]?.endsWith('chat-surface.tsx')).toBe(true)
  })
})

/**
 * 2026-10-21 用户反馈：「切换 Agent 中间没有动画过渡态」「刷新后再进日程秘书
 * 什么东西都没有了」。这一面（ChatSurface）才是 Agent 视图那一面——
 * 加载态、空态都补在这里。
 */
describe('ChatSurface — 切换时的加载态与空态（WP-AGENT-RAIL-LIVE 续）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setGatewayState('open')
  })

  afterEach(() => {
    cleanup()
    setGatewayState('idle')
  })

  it('进入中挂出过渡节点（带 Agent 名与转圈）', async () => {
    const { $agentEnter } = await import('@/app/chat/agent-enter-store')
    const { beginEntering } = await import('@/app/chat/agent-enter')

    $agentEnter.set(beginEntering('日程秘书'))

    try {
      renderSurface(<ChatSurface scope={agentScope('agenda')} />)

      const overlay = screen.getByTestId('chat-swap-overlay')
      // 可见性由 opacity 控制（aria-hidden 只在失败态才翻成 false）——
      // 钉的是「进入中它就在场上，并且念的是这个 Agent 的名字」。
      expect(overlay.className).toContain('opacity-100')
      expect(overlay.textContent).toContain('日程秘书')
    } finally {
      $agentEnter.set({ detail: '', label: null, reasonKey: null, status: 'idle' })
    }
  })

  it('空草稿要说话，不许一片纯白', async () => {
    const { setConsoleAgents } = await import('@/app/console/store/agents')

    setConsoleAgents([
      { category: 'butler', id: 'agenda', name: '日程秘书', profile: 'l2-agenda', status: 'idle' } as never
    ])

    renderSurface(<ChatSurface scope={agentScope('agenda')} />)

    const empty = screen.getByTestId('chat-surface-empty')
    // 名字在这里（locale 无关）；提示那句跟着当前语言走（测试环境是 en），
    // 所以只钉「除了名字之外还有一段提示」，不把它钉成某一种译文。
    expect(empty.textContent).toContain('日程秘书')
    expect(empty.querySelectorAll('p').length).toBe(2)
  })

  it('有消息时不显示空态', async () => {
    const { setMessages } = await import('@/store/session')

    setMessages([
      // ChatMessage 的形状是 id/role/**parts**（不是 content）——喂错形状会在
      // toBranchableMessageRepository 里炸，测到的就不是空态那件事了。
      { id: 'm1', parts: [{ text: '你好', type: 'text' }], role: 'user' } as never
    ])

    try {
      renderSurface(<ChatSurface scope={agentScope('agenda')} />)
      expect(screen.queryByTestId('chat-surface-empty')).toBeNull()
    } finally {
      setMessages([])
    }
  })
})
