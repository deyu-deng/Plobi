import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { LongTip, Tip } from './tooltip'

// jsdom 没有 ResizeObserver；Radix 的 popper 一打开就要它。
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    disconnect(): void {}
    observe(): void {}
    unobserve(): void {}
  }

  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
}

describe('Tip', () => {
  afterEach(() => {
    cleanup()
  })

  it('shows on pointer enter and dismisses on pointer leave', async () => {
    render(
      <Tip label="Layout editor — ⌘-click resets the layout">
        <button type="button">layout</button>
      </Tip>
    )

    const trigger = screen.getByRole('button', { name: 'layout' })

    fireEvent.pointerMove(trigger, { pointerType: 'mouse' })
    expect((await screen.findByRole('tooltip')).textContent).toContain(
      'Layout editor — ⌘-click resets the layout'
    )

    fireEvent.pointerLeave(trigger)
    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).toBeNull()
    })
  })

  it('never captures pointer events on the tip surface', async () => {
    render(
      <Tip label="Blocked?">
        <button type="button">target</button>
      </Tip>
    )

    fireEvent.pointerMove(screen.getByRole('button', { name: 'target' }), { pointerType: 'mouse' })
    const tip = await screen.findByRole('tooltip')
    // Role lives on the visually-hidden a11y node; the portaled content root
    // is the data-slot wrapper that must stay click-through.
    const content = tip.closest('[data-slot="tooltip-content"]') ?? tip.parentElement
    expect(content?.className).toMatch(/pointer-events-none/)
  })

  it('renders the child alone when label is empty', () => {
    render(
      <Tip label="">
        <button type="button">bare</button>
      </Tip>
    )

    expect(screen.getByRole('button', { name: 'bare' })).toBeTruthy()
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})

describe('LongTip', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders the child alone when label is empty', () => {
    const { container } = render(
      <LongTip label="">
        <span>一行字</span>
      </LongTip>
    )

    expect(container.innerHTML).toBe('<span>一行字</span>')
  })

  it('keeps the whole text off the native title and inside a scrollable surface', async () => {
    const long = '很长的正文，一行放不下。'.repeat(200)

    // hover 意图在 jsdom 里不触发（Radix 的 openDelay 等不到 pointerenter），
    // 所以这一格用受控 open 打开浮层 —— 断言的是「打开之后长什么样」，而
    // 「怎么打开」交给 Radix 自己。
    render(
      <LongTip label={long} open>
        <span>一行字</span>
      </LongTip>
    )

    const trigger = screen.getByText('一行字')
    // 原生 title 在 Electron 里对长文会截断 —— 这一格不许再出现它。
    expect(trigger.closest('[title]')).toBeNull()

    const tip = await waitFor(() => {
      const node = document.querySelector<HTMLElement>('[data-slot="long-tip-content"]')

      expect(node).toBeTruthy()

      return node as HTMLElement
    })

    expect(tip.textContent).toContain(long)
    // 全文得能滚、能选中，否则「看全部」仍是空话。
    expect(tip.className).toMatch(/overflow-auto/)
    expect(tip.className).toMatch(/select-text/)
    expect(tip.className).toMatch(/whitespace-pre-wrap/)
    // 高度要封顶，否则几百字会把浮层拉到屏幕外。
    expect(tip.className).toMatch(/max-h-/)
  })
})
