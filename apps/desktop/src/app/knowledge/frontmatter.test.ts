import { describe, expect, it } from 'vitest'

import { parseFrontmatter } from './frontmatter'

/**
 * 属性面板读的是 frontmatter。解析器只认**顶层那几格**（盘上笔记真在用的形状），
 * 认不出的不假装解析成功。
 */
describe('parseFrontmatter', () => {
  it('没有 frontmatter 就返回空', () => {
    expect(parseFrontmatter('# 标题\n\n正文。')).toEqual([])
  })

  it('键值对一行一格', () => {
    const entries = parseFrontmatter(['---', 'title: 微积分', 'belongs: []', '---', '', '正文。'].join('\n'))

    expect(entries).toEqual([
      { key: 'title', value: '微积分' },
      { key: 'belongs', value: '' }
    ])
  })

  it('行内列表收成一行', () => {
    const entries = parseFrontmatter(['---', 'tags: [数学, 极限]', '---'].join('\n'))

    expect(entries[0]).toEqual({ key: 'tags', value: '数学、极限' })
  })

  it('多行列表也收成一行', () => {
    const entries = parseFrontmatter(['---', 'relates:', '  - 微积分', '  - 线性代数', '---'].join('\n'))

    expect(entries[0]).toEqual({ key: 'relates', value: '微积分、线性代数' })
  })

  it('坏掉的 frontmatter 当没有（不抛）', () => {
    expect(parseFrontmatter('---\ntitle: 微积分\n没有收尾')).toEqual([])
  })
})
