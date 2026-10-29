import { describe, expect, it } from 'vitest'

import { gestureTargetOk } from './use-popout-drag'

/** 造一颗挂在 composer 根里的元素（手势只在根内生效）。 */
function el(tag: string, attrs: Record<string, string>, root: HTMLElement): HTMLElement {
  const node = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, v)
  }
  root.appendChild(node)
  return node
}

describe('composer 拖拽手势的靶区', () => {
  it('引用条不参与手势：拖它的文字不许把整颗 composer 剥离泊位（2026-10-29 用户报的冲突）', () => {
    const root = document.createElement('div')
    root.setAttribute('data-slot', 'composer-root')
    document.body.appendChild(root)

    const quoteSpan = el('span', { 'data-slot': 'composer-quote' }, root)
    const insideQuote = el('span', {}, quoteSpan)
    const clearButton = el('button', { 'aria-label': 'Clear quote' }, quoteSpan)

    expect(gestureTargetOk(insideQuote)).toBe(false)
    expect(gestureTargetOk(quoteSpan)).toBe(false)
    // × 本来就被 button 那条排除，这里钉住别退化
    expect(gestureTargetOk(clearButton)).toBe(false)

    root.remove()
  })

  it('其余手势靶区照旧：正文与抓握边仍可拖', () => {
    const root = document.createElement('div')
    root.setAttribute('data-slot', 'composer-root')
    root.setAttribute('data-popped-out', '')
    document.body.appendChild(root)

    expect(gestureTargetOk(root)).toBe(true)
    expect(gestureTargetOk(el('div', { 'data-slot': 'composer-drag-region' }, root))).toBe(true)

    root.remove()
  })

  it('按钮与输入件照旧不参与手势', () => {
    const root = document.createElement('div')
    root.setAttribute('data-slot', 'composer-root')
    document.body.appendChild(root)

    expect(gestureTargetOk(el('button', {}, root))).toBe(false)
    expect(gestureTargetOk(el('textarea', {}, root))).toBe(false)
    expect(gestureTargetOk(null)).toBe(false)

    root.remove()
  })
})
