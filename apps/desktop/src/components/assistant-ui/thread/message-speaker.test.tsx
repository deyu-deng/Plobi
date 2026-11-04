import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'

import { en } from '@/i18n/en'

import { Thread } from '.'
import type { ThreadSpeaker } from './types'

/**
 * 消息署名（用户 11-03：「以后消息要加在气泡上面旁边或者侧边加上 Agent 的名字和头像。
 * 用户的也要……」）。这一档管三件事：
 * ① 他自己打的那条——名字（中文那一屏就是他拍的那一个「我」）+ 中性人形头像；
 * ② **秘书代他问的那条不许也署成他**（用户 11-04 指着截图问：「为什么总秘书发的消息的
 *    头像也是我呢？」）：不写他的名字，头像换机器人，来源那一行仍然是"秘书代我发起"；
 * ③ Agent 那一条的名字与头像由挂 `Thread` 的那一面递进来（`chat-surface.tsx` 从名册取）。
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

function userMessage(id: string, askedBy?: string): ThreadMessage {
  return {
    attachments: [],
    content: [{ text: `hello ${id}`, type: 'text' }],
    createdAt,
    id,
    metadata: { custom: askedBy ? { askedBy } : {} },
    role: 'user'
  } as ThreadMessage
}

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

describe('消息两侧的署名', () => {
  it('他自己打的那一条：有名字，头像用人形', () => {
    const { container } = render(<Harness messages={[userMessage('own')]} />)

    // 先断"这一行有字"，再断是哪个字：少一枚头像或少一行字都必须红，不能让 `undefined`
    // 与目录里还没落的那一格互相抵消成假绿。jsdom 这一侧跑的是 en 目录，所以判据读目录。
    expect(container.querySelector('[data-slot="aui_user-byline"]')?.textContent).toBe(en.assistant.thread.me)
    expect(container.querySelector('[data-slot="aui_user-avatar"]')?.innerHTML).toContain('codicon-person')
  })

  it('秘书代问的那一条：不署成他，头像换机器人', () => {
    const { container } = render(<Harness messages={[userMessage('relayed', 'l1')]} />)

    expect(container.querySelector('[data-slot="aui_user-byline"]')).toBeNull()
    expect(container.querySelector('[data-slot="aui_user-avatar"]')?.innerHTML).toContain('codicon-hubot')
    // 来源那一行仍然在——它说的是"谁代他发起"，与署名是两件事。
    expect(container.querySelector('[data-slot="aui_user-asked-by"]')?.textContent).toBe(
      en.assistant.thread.askedBySecretary
    )
  })

  it('刚进来的代问（两列还没落到 metadata）也不署成他：读正文那一行的前缀', () => {
    const { container } = render(
      <Harness
        messages={[
          {
            attachments: [],
            content: [{ text: '【plobi代问】\n提问人：plobi——替他统筹各项目的那一个。', type: 'text' }],
            createdAt,
            id: 'live-relay',
            metadata: { custom: {} },
            role: 'user'
          } as ThreadMessage
        ]}
      />
    )

    expect(container.querySelector('[data-slot="aui_user-byline"]')).toBeNull()
    // 外部说话人那一格：名字是前缀里的那一个，不是「You」。
    expect(container.querySelector('[data-slot="aui_message-byline"]')?.textContent).toBe('plobi')
  })

  it('Agent 那一条：拿到名字就署名，并带上那一枚头像', () => {
    const { container } = render(
      <Harness messages={[ASSISTANT_MESSAGE]} speaker={{ color: '#7C3AED', icon: 'rocket', name: 'plobi-research' }} />
    )

    const byline = container.querySelector('[data-slot="aui_assistant-byline"]')

    expect(byline?.textContent).toContain('plobi-research')
    // 头像用的是名册那一行自己的字形与颜色（与左栏那一列同一份），没图标退回 hubot。
    expect(byline?.querySelector('[data-slot="aui_assistant-avatar"]')).toBeTruthy()
    expect(byline?.querySelector('span')?.getAttribute('style')).toContain('rgb(124, 58, 237)')
  })

  it('没给名字就不署名：不画一行空的抬头占位', () => {
    const { container } = render(<Harness messages={[ASSISTANT_MESSAGE]} />)

    expect(container.querySelector('[data-slot="aui_assistant-byline"]')).toBeNull()
  })
})
