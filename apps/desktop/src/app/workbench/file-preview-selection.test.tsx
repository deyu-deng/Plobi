import { describe, expect, it } from 'vitest'

import { droppedFileInlineRef } from '@/app/chat/composer/inline-refs'

import { lineRangeForSelection, selectedLineRange } from './file-preview'

/**
 * 文件侧：选区 ⇒ 行号 ⇒ 行段引用（WP-SELECTION-TOOLBAR）。
 *
 * 判据是**同形**：选区算出来的那枚引用，与行号槽选行（⌘/Ctrl+L 那条路）算出来的
 * 必须是同一个形状——不然「让智能体改这一段」引到的行会跟人看到的不一样。
 */

function chunk(text: string, start: number) {
  const holder = document.createElement('div')

  holder.setAttribute('data-chunk-start', String(start))
  holder.textContent = text
  document.body.appendChild(holder)

  return holder
}

describe('selectedLineRange', () => {
  it('选区从第几行开始：数它前面有几行', () => {
    expect(selectedLineRange(0, '', '第一行')).toEqual({ line: 1, lineEnd: 1 })
    expect(selectedLineRange(0, '第一行\n', '第二行')).toEqual({ line: 2, lineEnd: 2 })
  })

  it('选区跨了几行，末行就往后推几行', () => {
    expect(selectedLineRange(0, 'a\nb\n', 'c\nd')).toEqual({ line: 3, lineEnd: 4 })
  })

  it('代码块不是第一块时，行号接着往下数', () => {
    expect(selectedLineRange(200, 'x\n', 'y')).toEqual({ line: 202, lineEnd: 202 })
  })
})

describe('lineRangeForSelection', () => {
  it('选区落在代码块里 ⇒ 算出这个文件里的行号', () => {
    const holder = chunk('第一行\n第二行\n第三行', 0)
    const textNode = holder.firstChild as Text
    const range = document.createRange()

    range.setStart(textNode, 4)
    range.setEnd(textNode, 7)

    expect(lineRangeForSelection(range)).toEqual({ line: 2, lineEnd: 2 })

    holder.remove()
  })

  it('选区不在代码块里 ⇒ 没有行号（不猜）', () => {
    const outside = document.createElement('p')

    outside.textContent = '不是代码'
    document.body.appendChild(outside)

    const range = document.createRange()

    range.selectNodeContents(outside)

    expect(lineRangeForSelection(range)).toBeNull()

    outside.remove()
  })

  it('产出的引用与行号槽选行那条同形', () => {
    const filePath = 'D:/work/app.py'
    const cwd = 'D:/work'

    const holder = chunk('一\n二\n三\n四', 0)
    const textNode = holder.firstChild as Text
    const range = document.createRange()

    // 选中第二行到第三行。
    range.setStart(textNode, 2)
    range.setEnd(textNode, 5)

    const lines = lineRangeForSelection(range)

    expect(lines).toEqual({ line: 2, lineEnd: 3 })
    expect(droppedFileInlineRef({ line: lines!.line, lineEnd: lines!.lineEnd, path: filePath }, cwd)).toBe(
      droppedFileInlineRef({ line: 2, lineEnd: 3, path: filePath }, cwd)
    )
    expect(droppedFileInlineRef({ line: lines!.line, lineEnd: lines!.lineEnd, path: filePath }, cwd)).toContain(
      '@line:'
    )

    holder.remove()
  })
})
