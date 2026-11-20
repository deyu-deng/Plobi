// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { $composerAttachments } from '@/store/composer'
import { removeQuoteTargets, setQuoteTarget } from '@/store/quote'

import {
  chipElementFrom,
  ChipPreviewCard,
  chipPreviewFor,
  chipPreviewPosition,
  useChipPreview
} from './composer-chip-preview'
import { renderComposerContents } from './rich-editor'

/**
 * chip 的**悬浮预览卡**（用户 11-19：「用鼠标悬浮可以查看引用附件的预览」）。
 *
 * 这一档钉的是**卡上摆什么**：
 * ① 引用 ⇒ 被引的那段**原文**（chip 标签是截断的，这里给全文）；
 * ② 图 ⇒ 那张图；
 * ③ 文件 ⇒ 它的名字与路径；
 * ④ 取不到真源 ⇒ **不出卡**（空卡比没卡更糟）；
 * ⑤ 卡摆的位置：贴着 chip，上面放不下就放下面，左边不越界。
 *
 * 卡上**没有新句子**（全是数据）⇒ 四语一个字不动。
 */

afterEach(() => {
  cleanup()
  removeQuoteTargets(['12', '34'])
  $composerAttachments.set([])
})

describe('卡上摆什么', () => {
  it('引用 ⇒ 被引的那段原文（全文）', () => {
    setQuoteTarget({ id: '12', text: '被引的那段话，第一行\n第二行' })

    const preview = chipPreviewFor('quote', '12')

    expect(preview?.body).toBe('被引的那段话，第一行\n第二行')
  })

  it('引用的原文取不到 ⇒ 不出卡', () => {
    expect(chipPreviewFor('quote', '34')).toBeNull()
  })

  it('图 ⇒ 那张图 ＋ 它的名字', () => {
    $composerAttachments.set([
      {
        id: 'a1',
        kind: 'image',
        label: 'shot.png',
        detail: 'D:\\tmp\\shot.png',
        path: 'D:\\tmp\\shot.png',
        previewUrl: 'data:image/png;base64,AAA'
      }
    ])

    const preview = chipPreviewFor('image', 'D:\\tmp\\shot.png')

    expect(preview?.imageUrl).toBe('data:image/png;base64,AAA')
    expect(preview?.title).toBe('shot.png')
    expect(preview?.body).toBe('D:\\tmp\\shot.png')
  })

  it('文件 ⇒ 名字与路径', () => {
    $composerAttachments.set([
      {
        id: 'a2',
        kind: 'file',
        label: 'notes.md',
        detail: 'docs/notes.md',
        path: 'D:\\work\\docs\\notes.md',
        refText: '@file:`docs/notes.md`'
      }
    ])

    const preview = chipPreviewFor('file', 'docs/notes.md')

    expect(preview?.title).toBe('notes.md')
    expect(preview?.body).toBe('docs/notes.md')
  })

  it('kind 对不上就不捡（`@file:` 的 chip 不捡同一路径的图）', () => {
    $composerAttachments.set([
      { id: 'a3', kind: 'image', label: 'shot.png', path: 'D:\\tmp\\shot.png', previewUrl: 'data:image/png;base64,AAA' }
    ])

    const preview = chipPreviewFor('file', 'D:\\tmp\\shot.png')

    expect(preview?.imageUrl).toBeUndefined()
    expect(preview?.title).not.toBe('shot.png')
  })

  it('没有附件记录的那些 chip ⇒ 至少把值本身摆出来', () => {
    expect(chipPreviewFor('file', 'src/a.ts')?.title).toBe('src/a.ts')
  })
})

describe('找那枚 chip', () => {
  it('从 chip 里的那个字也能找到 chip', () => {
    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@quote:`12` 你看')

    const chip = editor.querySelector('[data-ref-kind="quote"]') as HTMLElement
    const label = chip?.firstElementChild as HTMLElement

    expect(chipElementFrom(label)).toBe(chip)
    expect(chipElementFrom(editor)).toBeNull()
  })
})

describe('卡摆哪儿', () => {
  it('上面放得下 ⇒ 放上面；放不下 ⇒ 放下面', () => {
    const viewport = { width: 1000, height: 800 }

    const above = chipPreviewPosition({ top: 400, bottom: 420, left: 100 }, viewport)

    expect(above.bottom).toBe(800 - 400 + 8)
    expect(above.top).toBeUndefined()

    const below = chipPreviewPosition({ top: 40, bottom: 60, left: 100 }, viewport)

    expect(below.top).toBe(68)
    expect(below.bottom).toBeUndefined()
  })

  it('左边不越界', () => {
    const viewport = { width: 1000, height: 800 }

    expect(chipPreviewPosition({ top: 400, bottom: 420, left: -50 }, viewport).left).toBe(8)
    expect(chipPreviewPosition({ top: 400, bottom: 420, left: 990 }, viewport).left).toBe(1000 - 352 - 8)
  })
})

describe('那张卡', () => {
  it('卡上出现被引的原文', () => {
    const editor = document.createElement('div')

    document.body.append(editor)
    renderComposerContents(editor, '@quote:`12`')

    const chip = editor.querySelector('[data-ref-kind="quote"]') as HTMLElement

    render(
      <ChipPreviewCard
        chip={chip}
        onDismiss={() => undefined}
        preview={{ kind: 'quote', value: '12', body: '被引的那段话' }}
      />
    )

    expect(document.body.textContent).toContain('被引的那段话')
    expect(document.querySelector('[data-slot="composer-chip-preview"]')).toBeTruthy()
  })
})

/** 最小输入面：编辑区里放一枚 chip，手势挂在编辑区上（跟真输入面同形）。 */
function ComposerHarness() {
  const preview = useChipPreview()
  const editorRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (editorRef.current) {
      renderComposerContents(editorRef.current, '@quote:`12` 你看')
    }
  }, [])

  return (
    <div onMouseOut={preview.onMouseOut} onMouseOver={preview.onMouseOver}>
      <div contentEditable ref={editorRef} />
      {preview.card}
    </div>
  )
}

describe('接线（鼠标停在那枚 chip 上）', () => {
  it('出卡；移开就收', () => {
    setQuoteTarget({ id: '12', text: '被引的那段话' })

    const { container } = render(<ComposerHarness />)
    const chip = container.querySelector('[data-ref-kind="quote"]') as HTMLElement

    expect(document.querySelector('[data-slot="composer-chip-preview"]')).toBeNull()

    fireEvent.mouseOver(chip)

    expect(document.querySelector('[data-slot="composer-chip-preview"]')).toBeTruthy()
    expect(document.body.textContent).toContain('被引的那段话')

    fireEvent.mouseOut(chip, { relatedTarget: document.body })

    expect(document.querySelector('[data-slot="composer-chip-preview"]')).toBeNull()
  })

  it('停在普通文字上不出卡', () => {
    const { container } = render(<ComposerHarness />)
    const editor = container.querySelector('div[contenteditable]') as HTMLElement

    fireEvent.mouseOver(editor)

    expect(document.querySelector('[data-slot="composer-chip-preview"]')).toBeNull()
  })
})
