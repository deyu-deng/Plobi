import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'

import { en } from '@/i18n/en'

import { Thread } from '.'
import type { ThreadSpeaker } from './types'

/**
 * 消息署名（用户 11-03：「以后消息要加在气泡上面旁边或者侧边加上 Agent 的名字和头像。
 * 用户的也要……鼠标悬浮在气泡旁边时下方出现编辑、复制和引用等符号」）。这一档管两侧：
 * 他自己那一条的名字来自目录（中文那一屏就是他拍的那一个「我」），Agent 那一条的名字
 * 由挂 `Thread` 的那一面给（`chat-surface.tsx` 从名册取那一行）。
 */

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
  window.setTimeout(() => callback(performance.now()), 0)
)
vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))

Element.prototype.scrollTo = function scrollTo() {}

const createdAt = new Date('2026-05-01T00:00:00.000Z')

const MESSAGES: ThreadMessage[] = [
  {
    attachments: [],
    content: [{ text: 'hello', type: 'text' }],
    createdAt,
    id: 'user-1',
    metadata: { custom: {} },
    role: 'user'
  } as ThreadMessage,
  {
    content: [{ text: 'done', type: 'text' }],
    createdAt,
    id: 'assistant-1',
    metadata: {
      custom: {},
      steps: [],
      unstable_annotations: [],
      unstable_data: [],
      unstable_state: null
    },
    role: 'assistant',
    status: { reason: 'stop', type: 'complete' }
  } as ThreadMessage
]

function Harness({ speaker }: { speaker?: ThreadSpeaker }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    isRunning: false,
    messages: MESSAGES,
    onEdit: async () => {},
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread assistantSpeaker={speaker} />
    </AssistantRuntimeProvider>
  )
}

describe('消息两侧的署名', () => {
  it('他自己那一条：头像旁边有那一个名字', () => {
    const { container } = render(<Harness />)

    // 先断"这一行有字"，再断是哪个字：少一枚头像或少一行字都必须红，不能让 `undefined`
    // 与目录里还没落的那一格互相抵消成假绿。jsdom 这一侧跑的是 en 目录，所以判据读目录。
    expect(container.querySelector('[data-slot="aui_user-avatar"]')).toBeTruthy()

    const byline = container.querySelector('[data-slot="aui_user-byline"]')?.textContent

    expect(byline).toBeTruthy()
    expect(byline).toBe(en.assistant.thread.me)
  })

  it('Agent 那一条：拿到名字就署名，并带上那一枚头像', () => {
    const { container } = render(<Harness speaker={{ color: '#7C3AED', icon: 'rocket', name: 'plobi-research' }} />)

    const byline = container.querySelector('[data-slot="aui_assistant-byline"]')

    expect(byline?.textContent).toContain('plobi-research')
    // 头像用的是名册那一行自己的字形与颜色（与左栏那一列同一份），没图标退回 hubot。
    expect(byline?.querySelector('[data-slot="aui_assistant-avatar"]')).toBeTruthy()
    expect(byline?.querySelector('span')?.getAttribute('style')).toContain('rgb(124, 58, 237)')
  })

  it('没给名字就不署名：不画一行空的抬头占位', () => {
    const { container } = render(<Harness />)

    expect(container.querySelector('[data-slot="aui_assistant-byline"]')).toBeNull()
  })
})
