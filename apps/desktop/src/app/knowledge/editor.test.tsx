// @vitest-environment jsdom
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { countWords, NoteEditor, noteEditorView } from './editor'

/**
 * 编辑器这一族（WP-NOTES-EDITOR）：用户 11-10 22:4x 要的是「能正经写长笔记」，
 * 那个裸 textarea 给不了的东西。这一组钉三件事：
 * ① 字数怎么算（中文按字、英文按词——Obsidian 就是这么数的）
 * ② 编辑器真的挂出来了（CodeMirror 的 `.cm-editor` ＋行号槽）
 * ③ 状态行报的是**活着的**读数（改了字，字数跟着变）
 */
describe('countWords', () => {
  it('中文按字算，英文按词算', () => {
    expect(countWords('量子力学')).toBe(4)
    expect(countWords('hello world')).toBe(2)
    expect(countWords('量子 hello 世界')).toBe(5)
  })

  it('标点不算字', () => {
    expect(countWords('你好，世界。')).toBe(4)
  })
})

describe('NoteEditor', () => {
  it('挂出编辑器与行号槽', async () => {
    const { container } = render(<NoteEditor initialValue="# 标题" onChange={() => {}} />)

    await waitFor(() => expect(container.querySelector('.cm-editor')).not.toBeNull())
    expect(container.querySelector('.cm-gutters')).not.toBeNull()
    expect(container.textContent).toContain('标题')
  })

  it('状态行报字数与行数', async () => {
    const { container } = render(<NoteEditor initialValue={'一\n二\n三'} onChange={() => {}} />)

    await waitFor(() => expect(container.textContent).toContain('3 字'))
    expect(container.textContent).toContain('行 3')
  })

  it('改一个字就报一次新正文（上层据此判「未保存」）', async () => {
    const onChange = vi.fn()
    const { container } = render(<NoteEditor initialValue="初始" onChange={onChange} />)

    await waitFor(() => expect(container.querySelector('.cm-editor')).not.toBeNull())

    // 直接派发一次改动：CM 的输入层在 jsdom 里没有真键盘，测的是**回调链路**。
    const view = noteEditorView(container.querySelector('[data-testid="note-editor"]'))

    expect(view).toBeTruthy()
    view?.dispatch({ changes: { from: 0, insert: '初', to: 2 } })

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(onChange.mock.calls.at(-1)?.[0]).toBe('初')
  })
})
