// @vitest-environment jsdom
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { NoteMarkdown } from './note-markdown'
import { parseOutline } from './outline'

/**
 * 大纲靠 id 跳章，而 id 是渲染后按 DOM 顺序贴上去的。Streamdown 内部按块渲染，
 * 所以这里必须拿**真渲染**核一次：DOM 里的标题序列要和 parseOutline 的序列
 * 逐条相等（条数、文本、id）。用 mock 测不出这个错位。
 */
describe('NoteMarkdown', () => {
  it('aligns rendered heading ids with the outline, skipping frontmatter and fenced code', async () => {
    const text = [
      '---',
      'title: 测试笔记',
      '---',
      '',
      '# 测试笔记',
      '',
      '## 第1章 极限',
      '',
      '正文一段。',
      '',
      '### 1.1 数列极限',
      '',
      '```md',
      '# 这是代码块里的示例标题',
      '```',
      '',
      '#### 1. 定义',
      '',
      '## 第2章 导数',
      ''
    ].join('\n')

    const { container } = render(<NoteMarkdown text={text} />)
    await waitFor(() => expect(container.querySelectorAll('h1,h2,h3,h4,h5,h6').length).toBeGreaterThan(0))

    const outline = parseOutline(text)
    const headings = Array.from(container.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6'))

    expect(headings.length).toBe(outline.length)
    expect(headings.map(element => element.textContent?.trim())).toEqual(outline.map(item => item.text))
    expect(headings.map(element => element.id)).toEqual(outline.map(item => item.id))
    expect(container.textContent).not.toContain('title: 测试笔记')
  })

  it('gives repeated headings distinct ids', () => {
    const text = '## 小结\n\n## 小结\n\n## 小结\n'
    const outline = parseOutline(text)
    expect(new Set(outline.map(item => item.id)).size).toBe(3)
  })
})
