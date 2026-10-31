import { describe, expect, it } from 'vitest'

import { DOCK_ICON_WIDTH, DOCK_OVERFLOW_WIDTH, DOCK_TEXT_WIDTH, dockFitCount, dockItemWidth } from './dock-layout'

const icon = DOCK_ICON_WIDTH
const text = DOCK_TEXT_WIDTH
const overflow = DOCK_OVERFLOW_WIDTH

describe('dockFitCount', () => {
  it('全部装得下时不截断', () => {
    expect(dockFitCount([icon, icon, icon], icon * 3)).toBe(3)
  })

  it('装不下时给「⋯」留出它自己那一格', () => {
    // 3 格差 1px 放不下；2 格 + 「⋯」一样差 1px ⇒ 只能退到 1 格 + 「⋯」
    expect(dockFitCount([icon, icon, icon], icon * 2 + overflow - 1)).toBe(1)
    // 刚好够 3 格 —— 不需要「⋯」，第 3 格不该被无端收走
    expect(dockFitCount([icon, icon, icon], icon * 3)).toBe(3)
  })

  it('一个都放不下时只留「⋯」', () => {
    expect(dockFitCount([icon], 20)).toBe(0)
    expect(dockFitCount([], 100)).toBe(0)
  })

  it('文字格按宽一档算', () => {
    expect(dockFitCount([text, text], text * 2)).toBe(2)
    expect(dockFitCount([text, icon], text + icon)).toBe(2)
    // 差 1px：两格 + 「⋯」都装不下，只能退到「⋯」
    expect(dockFitCount([text, icon], text + icon - 1)).toBe(0)
    // 三格文字只有 100：两格 + 「⋯」要 136，退到一格 + 「⋯」= 82
    expect(dockFitCount([text, text, text], 100)).toBe(1)
  })
})

describe('dockItemWidth', () => {
  it('文字格比图标格宽', () => {
    expect(dockItemWidth({ variant: 'text' })).toBe(text)
    expect(dockItemWidth({ variant: 'action' })).toBe(icon)
    expect(dockItemWidth({})).toBe(icon)
  })
})
