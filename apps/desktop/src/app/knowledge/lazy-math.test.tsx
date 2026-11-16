// @vitest-environment jsdom
import { render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { NoteMarkdown } from './note-markdown'

/**
 * 懒渲染那一族（WP-NOTES-VIEW-PERF）：公式**进视口才排**。
 *
 * 为什么钉它：长笔记（《大学物理》2342 个公式）整篇排一遍 KaTeX 是 346 ms，
 * 排出来的 DOM 约 7 万个节点 —— 打开一篇就冻一下。Obsidian 不冻，是因为它
 * 只排看得见的那几个。
 *
 * 这一族钉两件事：
 *   ① **没进视口就不排**（屏上先是一枚占位，源码不给人看）
 *   ② **进了视口就排**（占位变成 `.katex`）
 *   ③ 环境**没有** IntersectionObserver 时**立刻排**（公式不许永远不出现）
 */
class IntersectionObserverStub {
  static instances: IntersectionObserverStub[] = []

  callback: IntersectionObserverCallback
  observed: Element[] = []

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    IntersectionObserverStub.instances.push(this)
  }

  observe(element: Element): void {
    this.observed.push(element)
  }

  unobserve(): void {}

  disconnect(): void {}

  takeRecords(): IntersectionObserverEntry[] {
    return []
  }

  /** 让观察器看到「这些元素进视口了」。 */
  enter(): void {
    this.callback(
      this.observed.map(target => ({ isIntersecting: true, target } as IntersectionObserverEntry)),
      this as unknown as IntersectionObserver
    )
  }
}

afterEach(() => {
  IntersectionObserverStub.instances = []
  vi.unstubAllGlobals()
})

describe('NoteMarkdown 懒渲染', () => {
  it('没进视口的公式先留占位，不排版', async () => {
    vi.stubGlobal('IntersectionObserver', IntersectionObserverStub)

    const { container } = render(<NoteMarkdown text="动能 $E=mc^2$ 这样" />)

    await waitFor(() => expect(container.querySelector('[data-katex-lazy]')).not.toBeNull())
    expect(container.querySelector('.katex')).toBeNull()
  })

  it('进了视口才排版（占位变成 .katex）', async () => {
    vi.stubGlobal('IntersectionObserver', IntersectionObserverStub)

    const { container } = render(<NoteMarkdown text="动能 $E=mc^2$ 这样" />)

    await waitFor(() => expect(container.querySelector('[data-katex-lazy]')).not.toBeNull())

    for (const instance of IntersectionObserverStub.instances) {
      instance.enter()
    }

    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
    expect(container.querySelector('[data-katex-lazy]')).toBeNull()
  })

  it('环境没有 IntersectionObserver 时立刻排版（公式不许永远不出现）', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)

    const { container } = render(<NoteMarkdown text="动能 $E=mc^2$ 这样" />)

    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull())
  })
})
