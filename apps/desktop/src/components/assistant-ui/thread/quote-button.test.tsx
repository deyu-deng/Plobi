import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { QuoteButton } from './quote-button'

const setQuoteTarget = vi.fn()

vi.mock('@/store/quote', () => ({
  setQuoteTarget: (target: unknown) => setQuoteTarget(target),
  clearQuoteTarget: () => {}
}))

function stubSelection(text: string | null) {
  vi.stubGlobal(
    'getSelection',
    vi.fn(() => (text === null ? null : { toString: () => text }))
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setQuoteTarget.mockClear()
})

const TEXT = '今天的日程已经读到了，理论力学那门课往后挪了半小时'

describe('QuoteButton 的引用范围', () => {
  it('消息里有选区时只引选区（2026-10-29 用户令：不许只能整段引）', () => {
    stubSelection('理论力学那门课往后挪了半小时')
    const { getByLabelText } = render(<QuoteButton id="m-1" text={TEXT} />)

    fireEvent.pointerDown(getByLabelText('Quote'), { button: 0 })
    fireEvent.click(getByLabelText('Quote'))

    expect(setQuoteTarget).toHaveBeenCalledWith({ id: 'm-1', text: '理论力学那门课往后挪了半小时' })
  })

  it('选区不是这条消息的子串时，退回整条——不能把 A 的话挂到 B 的 id 上', () => {
    stubSelection('这是别的会话里选的一句话')
    const { getByLabelText } = render(<QuoteButton id="m-2" text={TEXT} />)

    fireEvent.click(getByLabelText('Quote'))

    expect(setQuoteTarget).toHaveBeenCalledWith({ id: 'm-2', text: TEXT })
  })

  it('没有选区时引整条', () => {
    stubSelection(null)
    const { getByLabelText } = render(<QuoteButton id="m-3" text={TEXT} />)

    fireEvent.click(getByLabelText('Quote'))

    expect(setQuoteTarget).toHaveBeenCalledWith({ id: 'm-3', text: TEXT })
  })

  it('点按钮之前先把 pointerdown 拦下，选区才不会被点塌', () => {
    stubSelection('理论力学')
    const { getByLabelText } = render(<QuoteButton id="m-4" text={TEXT} />)
    const button = getByLabelText('Quote')
    const event = new MouseEvent('pointerdown', { button: 0, bubbles: true, cancelable: true })
    button.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)

    fireEvent.click(button)
    expect(setQuoteTarget).toHaveBeenCalledWith({ id: 'm-4', text: '理论力学' })
  })
})
