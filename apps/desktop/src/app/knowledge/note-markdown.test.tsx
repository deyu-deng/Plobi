// @vitest-environment jsdom
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { NoteMarkdown, makeLinkIndex } from './note-markdown'

/**
 * wikilink 必须**真渲染**出来才算数——mock 测不出 remark 插件有没有生效、
 * 组件映射对不对。这里用真 DOM 核三件事：
 * ① `[[CATIA]]` 变成按钮，不是普通文字
 * ② 代码围栏里的 `[[…]]` 不被当成链接（AST 层面天然成立）
 * ③ 库里没有的目标被标成「未创建」，点不动
 */
const NOTES = [
  { path: 'Notes/工具/Modeling/Creo.md', title: 'Creo Parametric' },
  { path: 'Notes/数学/微积分.md', title: '微积分' }
]

describe('NoteMarkdown wikilink', () => {
  it('renders a cross-file wikilink as a button', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="看 [[Creo]] 这个" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    const button = container.querySelector('button')
    expect(button?.textContent).toBe('Creo')
  })

  it('renders an alias', async () => {
    const { container } = render(
      <NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[微积分|微积分笔记]]" />
    )
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    expect(container.querySelector('button')?.textContent).toBe('微积分笔记')
  })

  it('marks a link to a note that does not exist as unresolvable', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[CATIA]]" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    const button = container.querySelector('button') as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('does not turn wikilinks inside fenced code into links', async () => {
    const text = ['```md', '![[image.png|300]]', '```'].join('\n')
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text={text} />)
    await waitFor(() => expect(container.textContent).toContain('image.png'))
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders an embed as a quiet badge, not a link', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="![[image.png|300]]" />)
    await waitFor(() => expect(container.textContent).toContain('image.png'))
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders a same-file anchor link as a button', async () => {
    const { container } = render(<NoteMarkdown linkIndex={makeLinkIndex(NOTES)} text="[[#ch1|1. 发展历程]]" />)
    await waitFor(() => expect(container.querySelector('button')).toBeTruthy())
    expect(container.querySelector('button')?.textContent).toBe('1. 发展历程')
  })
})
