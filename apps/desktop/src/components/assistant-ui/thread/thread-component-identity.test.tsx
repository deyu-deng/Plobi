import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ThreadSpeaker } from './types'

import { Thread } from '.'

/**
 * 消息组件的**身份**必须稳。
 *
 * assistant-ui 按组件类型决定这一条是「更新」还是「挂」——身份一变，React 把整条
 * 转录拆掉重挂。实测（开发态探针，160 条转录）：每来一次上层重渲染，80 个气泡连同
 * Markdown 块、复制按钮、图标全部重建（上千个 DOM 节点），一秒 2.7 次 ⇒ 屏上
 * 「一直在闪」、渲染进程常驻一个核、内存一路涨。
 *
 * 所以：署名字面上换一份对象、内容没变时，气泡**不许**被拆掉重挂；
 * 内容真变了，那一行署名**要**跟着变。
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

const ASSISTANT_MESSAGE: ThreadMessage = {
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

function Harness({ messages, speaker }: { messages: ThreadMessage[]; speaker?: ThreadSpeaker }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    isRunning: false,
    messages,
    onEdit: async () => {},
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread assistantSpeaker={speaker} />
    </AssistantRuntimeProvider>
  )
}

describe('消息组件的身份', () => {
  it('署名换一份对象、内容没变 ⇒ 那一条回答不被拆掉重挂', () => {
    const messages = [ASSISTANT_MESSAGE]

    const { container, rerender } = render(
      <Harness messages={messages} speaker={{ color: '#7C3AED', icon: 'rocket', name: 'plobi-research' }} />
    )

    const byline = container.querySelector('[data-slot="aui_assistant-byline"]')

    expect(byline?.textContent).toContain('plobi-research')
    expect(byline).not.toBeNull()

    // 同一份内容，换一个对象身份（上层每次渲染都新建的那种）。
    rerender(
      <Harness messages={messages} speaker={{ color: '#7C3AED', icon: 'rocket', name: 'plobi-research' }} />
    )

    // 没被重挂 ⇒ 原来那个节点还在文档里。
    expect(byline?.isConnected).toBe(true)
  })

  it('署名内容真变了 ⇒ 那一行跟着变', () => {
    const messages = [ASSISTANT_MESSAGE]

    const { container, rerender } = render(
      <Harness messages={messages} speaker={{ color: '#7C3AED', icon: 'rocket', name: 'plobi-research' }} />
    )

    expect(container.querySelector('[data-slot="aui_assistant-byline"]')?.textContent).toContain('plobi-research')

    rerender(<Harness messages={messages} speaker={{ color: '#7C3AED', icon: 'rocket', name: 'renamed' }} />)

    expect(container.querySelector('[data-slot="aui_assistant-byline"]')?.textContent).toContain('renamed')
  })
})
