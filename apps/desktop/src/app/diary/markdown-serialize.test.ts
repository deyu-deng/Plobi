import { describe, expect, it } from 'vitest'

import { pmToMarkdown } from './markdown-serialize'

const paragraph = (text: string) => ({ type: 'paragraph', content: [{ type: 'text', text }] })

describe('pmToMarkdown', () => {
  it('renders headings and paragraphs with a blank line between blocks', () => {
    const md = pmToMarkdown({
      type: 'doc',
      content: [
        { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '今天' }] },
        paragraph('上午写代码。'),
        paragraph('下午散步。')
      ]
    })
    expect(md).toBe('## 今天\n\n上午写代码。\n\n下午散步。\n')
  })

  it('keeps inline marks', () => {
    const md = pmToMarkdown({
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: '很' },
            { type: 'text', text: '重要', marks: [{ type: 'bold' }] },
            { type: 'text', text: '也' },
            { type: 'text', text: '不错', marks: [{ type: 'italic' }] },
            { type: 'text', text: '走这里', marks: [{ type: 'link', attrs: { href: 'https://example.com' } }] }
          ]
        }
      ]
    })
    expect(md).toBe('很**重要**也*不错*[走这里](https://example.com)\n')
  })

  it('renders bullet, ordered, quote and code blocks', () => {
    const md = pmToMarkdown({
      type: 'doc',
      content: [
        {
          type: 'bulletList',
          content: [
            { type: 'listItem', content: [paragraph('甲')] },
            { type: 'listItem', content: [paragraph('乙')] }
          ]
        },
        {
          type: 'orderedList',
          attrs: { start: 1 },
          content: [
            { type: 'listItem', content: [paragraph('一')] },
            { type: 'listItem', content: [paragraph('二')] }
          ]
        },
        {
          type: 'blockquote',
          content: [paragraph('引用一句')]
        },
        {
          type: 'codeBlock',
          attrs: { language: 'python' },
          content: [{ type: 'text', text: 'print(1)\nprint(2)' }]
        }
      ]
    })
    expect(md).toBe('- 甲\n- 乙\n\n1. 一\n2. 二\n\n> 引用一句\n\n```python\nprint(1)\nprint(2)\n```\n')
  })

  it('renders nested lists under their parent item', () => {
    const md = pmToMarkdown({
      type: 'doc',
      content: [
        {
          type: 'bulletList',
          content: [
            {
              type: 'listItem',
              content: [
                paragraph('外层'),
                { type: 'bulletList', content: [{ type: 'listItem', content: [paragraph('内层')] }] }
              ]
            }
          ]
        }
      ]
    })
    expect(md).toBe('- 外层\n  - 内层\n')
  })

  it('returns an empty string for an empty document', () => {
    expect(pmToMarkdown({ type: 'doc', content: [paragraph('')] })).toBe('')
    expect(pmToMarkdown(null)).toBe('')
  })
})
