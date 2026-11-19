// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { DirectiveContent } from '@/components/assistant-ui/directive-text'
import { removeQuoteTargets, setQuoteTarget } from '@/store/quote'

import {
  chipsInText,
  messageBodyFromText,
  quoteChipLabel,
  quoteIdsInText,
  quoteIntoComposer,
  stripQuoteChips
} from './composer-chips'
import { onComposerInsertRefsRequest } from './focus'
import { type InlineRefInput, insertInlineRefsIntoEditor } from './inline-refs'
import { REF_RE, renderComposerContents } from './rich-editor'

/**
 * 引用长成**正文里的一枚 chip**（用户 11-19）。
 *
 * 用户 11-19 报障：「引用条删不掉」——鼠标移到 X 号旁边就变成「移动对话框」的
 * 抓手光标与斜纹，双击那一块还会把输入框弹出/收起。根因：那条灰条在输入面
 * **外面**，被输入面底下铺的那层「拖动区」（`absolute inset-0`，z-index: auto）
 * 盖住了 ⇒ 点 X 落到它身上。
 *
 * 现在的形状（用户 11-19 看过 WorkBuddy 之后拍板）：引用就是正文里的一枚 chip，
 * 跟文字同一行、跟别的附件一起按加入顺序排；发送时按正文顺序带上一串锚点。
 *
 * 这一档钉的是**那一枚 chip 与那条锚点链**：
 * ① 两处 chip 词表都认 `quote`（少一处 ⇒ chip 变裸文本）；
 * ② 引用动作插出来的是 chip，标签是被引的原文；
 * ③ **重绘之后标签还在**（chip 是按线上形态重建的，标签得从真源取回）；
 * ④ `@quote:` 的线上形态**不发给 agent**（它是元数据不是正文）；
 * ⑤ 锚点按正文顺序，一串；
 * ⑥ 只引用没打字 ⇒ 正文退回被引的那段原文（不发空消息）。
 */

afterEach(() => {
  cleanup()
  removeQuoteTargets(['12', '34', 'm-1'])
})

describe('chip 词表', () => {
  it('两处都认 `quote`（少一处 chip 就变裸文本）', () => {
    // ① composer 侧（`rich-editor.ts` 的 REF_RE）——正文里的 chip 靠它认出来。
    expect([...('@quote:`12`'.matchAll(REF_RE))].length).toBe(1)

    // ② 渲染侧（`directive-text.tsx`）——同一份词表：那边认得才能把 chip 画出来
    // （图标也走那张表：少了 `quote` ⇒ chip 画成一枚没有图形的方块）。
    const { container } = render(<DirectiveContent text="@quote:`12`" />)
    const chip = container.querySelector('[data-directive-type="quote"]')

    expect(chip).toBeTruthy()
    expect(chip?.querySelector('svg')).toBeTruthy()
  })
})

describe('引用动作', () => {
  it('插出来的是一枚 chip（kind=quote、value=消息 id）', async () => {
    const seen: InlineRefInput[][] = []
    const off = onComposerInsertRefsRequest(({ refs }) => seen.push(refs))

    quoteIntoComposer('12', '被引的那段话')
    // 那个事件是 `setTimeout(0)` 派发的 ⇒ 等它落地再收摊。
    await new Promise(resolve => setTimeout(resolve, 0))
    off()

    expect(seen[0]?.[0]).toMatchObject({ kind: 'quote', value: '12' })
  })

  it('标签是被引的原文（压成一行、截断）', () => {
    expect(quoteChipLabel('  被引的\n那段话  ')).toBe('被引的 那段话')
    expect(quoteChipLabel('x'.repeat(60)).length).toBeLessThanOrEqual(41)
  })

  it('chip 插在正文里（contenteditable 内部，跟文字同一行）', () => {
    const editor = document.createElement('div')

    editor.contentEditable = 'true'
    document.body.append(editor)
    insertInlineRefsIntoEditor(editor, [{ kind: 'quote', value: '12', label: '被引的那段话' }])

    const chip = editor.querySelector('[data-ref-kind="quote"]')

    expect(chip).toBeTruthy()
    expect(chip?.textContent).toContain('被引的那段话')
  })
})

describe('重绘之后标签还在', () => {
  beforeEach(() => {
    setQuoteTarget({ id: '12', text: '被引的那段话' })
  })

  it('按线上形态重建 chip 时，标签从引用那张表取回', () => {
    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@quote:`12` 你看workbuddy里面')

    const chip = editor.querySelector('[data-ref-kind="quote"]')

    expect(chip).toBeTruthy()
    expect(chip?.textContent).toContain('被引的那段话')
  })
})

describe('发出去的那句话', () => {
  it('`@quote:` 的线上形态剥掉了（它是元数据不是正文）', () => {
    expect(stripQuoteChips('@quote:`12` 你看')).toBe('你看')
    expect(stripQuoteChips('你看 @quote:`12`')).toBe('你看')
    expect(stripQuoteChips('你看 @quote:`12` 里面')).toBe('你看 里面')
  })

  it('别的 chip 不剥（后端认得 `@file:` 那些）', () => {
    expect(stripQuoteChips('@file:`src/a.ts` 看一下')).toBe('@file:`src/a.ts` 看一下')
  })

  it('锚点按正文顺序，一串', () => {
    expect(quoteIdsInText('@quote:`12` 中间 @quote:`34`')).toEqual(['12', '34'])
    expect(chipsInText('@quote:`12` @file:`a.ts`')).toEqual([
      { kind: 'quote', value: '12' },
      { kind: 'file', value: 'a.ts' }
    ])
  })

  it('只引用没打字 ⇒ 正文退回被引的那段原文（不发空消息）', () => {
    setQuoteTarget({ id: '34', text: '被引的那段话' })

    expect(messageBodyFromText('@quote:`34`')).toBe('被引的那段话')
    expect(messageBodyFromText('@quote:`34` 你看')).toBe('你看')
  })
})
