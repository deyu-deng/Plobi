import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $cardFocus, $cardFocusAnimating, setCardFocus } from './layout'

/**
 * 「只显示这一张卡」那 200ms 的动效窗口。
 *
 * 用户 11-10：「某张卡片点击最大化的时候，没有相应的动效」——
 * 病根之一是网格列宽**从来没声明过过渡**（`grid-template-columns` 是内联写的，
 * 不给 `transition` 就不会动）。仓里左栏那把尺（`$sidebarAnimating`）就是这么做的：
 * 只在点的那一下为真，别的时候一点延迟都不留（拖拽改宽不能被拖慢）。
 *
 * 这组判据钉的是**行为**：窗口亮起、到点自己熄灭、连点只留最后一次。
 */
describe('卡片最大化的动效窗口', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    $cardFocus.set(null)
    $cardFocusAnimating.set(false)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('放大：窗口亮着，240ms 后自己熄灭', () => {
    expect($cardFocusAnimating.get()).toBe(false)

    setCardFocus('chat')

    expect($cardFocus.get()).toBe('chat')
    expect($cardFocusAnimating.get()).toBe(true)

    vi.advanceTimersByTime(240)

    expect($cardFocusAnimating.get()).toBe(false)
  })

  it('还原：走同一个窗口（不是只有放大才有动效）', () => {
    setCardFocus('workbench')
    vi.advanceTimersByTime(240)
    expect($cardFocusAnimating.get()).toBe(false)

    setCardFocus(null)

    expect($cardFocusAnimating.get()).toBe(true)
    vi.advanceTimersByTime(240)
    expect($cardFocusAnimating.get()).toBe(false)
  })

  it('连点两次：计时器只留最后一次，不叠成两段', () => {
    setCardFocus('chat')
    vi.advanceTimersByTime(100)
    setCardFocus(null)

    vi.advanceTimersByTime(200)
    expect($cardFocusAnimating.get()).toBe(true)

    vi.advanceTimersByTime(40)
    expect($cardFocusAnimating.get()).toBe(false)
  })
})
