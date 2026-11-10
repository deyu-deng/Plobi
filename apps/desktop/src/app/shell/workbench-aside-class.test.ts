import { describe, expect, it } from 'vitest'

import { resolveWorkbenchAsideClass } from './workbench-aside-class'

/**
 * 工作面卡的外框：**顶栏让位跟形态走，不跟位置走**。
 *
 * 用户 11-10：「某张卡片点击最大化的时候……顶栏的高度还会变化。我无法接受。」
 *
 * 病根：那 34px 的 `pt-(--titlebar-height)` 是**右栏形态**要的（顶栏是浮层，
 * 右栏那条轨的顶边在 y=0）。卡片模式里画布自己已经让过（`styles.css:2028`
 * `padding-top: calc(var(--titlebar-height) + 6px)`），右栏形态里那 34px 由
 * `styles.css:2147-2156` 的 `padding-top: 0 !important` 兜掉；
 * 可**主区**不在那三条规则里 ⇒ 工作面卡被最大化进主区后没人兜 ⇒
 * 卡面顶部多出一条 34px 的空带 ⇒ 顶栏（抬头那条）看着变高了。
 *
 * 这组判据钉的是**规则**（哪一形态让哪一形态），不是源码文字。
 */
describe('工作面卡的外框：顶栏让位', () => {
  it('卡片模式：画布自己让过顶栏，卡再让一次就多出一条空带 ⇒ 不让', () => {
    const cls = resolveWorkbenchAsideClass({ panesFlipped: false, shellCards: true, swapped: false })

    expect(cls).not.toContain('pt-(--titlebar-height)')
    expect(cls).toContain('border-l')
  })

  it('非卡片模式：顶栏是浮层、轨道顶边在 y=0 ⇒ 卡要自己把顶栏那一条让出来', () => {
    const cls = resolveWorkbenchAsideClass({ panesFlipped: false, shellCards: false, swapped: false })

    expect(cls).toContain('pt-(--titlebar-height)')
  })

  it('互换态：工作面卡也在主区，本来就不带描边与让位', () => {
    const cls = resolveWorkbenchAsideClass({ panesFlipped: false, shellCards: true, swapped: true })

    expect(cls).not.toContain('pt-(--titlebar-height)')
    expect(cls).not.toContain('border-l')
    expect(cls).not.toContain('border-r')
  })

  it('换了边：描边与内高光换到另一侧，让位口径不变', () => {
    const flipped = resolveWorkbenchAsideClass({ panesFlipped: true, shellCards: true, swapped: false })

    expect(flipped).toContain('border-r')
    expect(flipped).not.toContain('pt-(--titlebar-height)')
  })
})
