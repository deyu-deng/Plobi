import { describe, expect, it } from 'vitest'

import {
  buildNoteLinkIndex,
  findWikiLinkSpans,
  findWikiLinks,
  resolveWikiNote
} from './wikilink'

describe('findWikiLinks', () => {
  it('reads a cross-file link', () => {
    const [link] = findWikiLinks('看 [[CATIA]] 这个')
    expect(link.kind).toBe('note')
    expect(link.target).toBe('CATIA')
    expect(link.label).toBe('CATIA')
  })

  it('reads an alias', () => {
    const [link] = findWikiLinks('[[笔记名|显示别名]]')
    expect(link.kind).toBe('note')
    expect(link.target).toBe('笔记名')
    expect(link.alias).toBe('显示别名')
    expect(link.label).toBe('显示别名')
  })

  it('reads a same-file anchor link', () => {
    const [link] = findWikiLinks('[[#ch1|1. 发展历程]]')
    expect(link.kind).toBe('anchor')
    expect(link.target).toBe('')
    expect(link.anchor).toBe('ch1')
    expect(link.label).toBe('1. 发展历程')
  })

  it('treats an image embed as embed, not a note link', () => {
    const [link] = findWikiLinks('![[image.png|300]]')
    expect(link.kind).toBe('embed')
    expect(link.target).toBe('image.png')
  })

  it('treats a bare image target as embed even without the bang', () => {
    const [link] = findWikiLinks('[[photo.jpg]]')
    expect(link.kind).toBe('embed')
  })

  it('splits target and anchor together', () => {
    const [link] = findWikiLinks('[[笔记名#标题]]')
    expect(link.kind).toBe('note')
    expect(link.target).toBe('笔记名')
    expect(link.anchor).toBe('标题')
  })

  it('ignores links inside fenced code', () => {
    const text = ['```md', '![[image.png|300]]  # 嵌入图片', '```'].join('\n')
    expect(findWikiLinks(text)).toHaveLength(0)
  })

  it('finds every link on a line', () => {
    const links = findWikiLinks('- [[#ch1|一]]\n- [[#ch2|二]]')
    expect(links).toHaveLength(2)
    expect(links.map(l => l.anchor)).toEqual(['ch1', 'ch2'])
  })

  it('reports the span of each link so the renderer can slice text', () => {
    const text = '看 [[CATIA]] 吧'
    const spans = findWikiLinkSpans(text)
    expect(spans).toHaveLength(1)
    expect(text.slice(spans[0].index, spans[0].index + spans[0].length)).toBe('[[CATIA]]')
  })
})

describe('buildNoteLinkIndex', () => {
  const notes = [
    { path: 'Notes/工具/Modeling/Creo.md', title: 'Creo Parametric' },
    { path: 'Notes/数学/微积分.md', title: '微积分' }
  ]

  it('matches by file stem', () => {
    const index = buildNoteLinkIndex(notes)
    expect(resolveWikiNote({ target: 'Creo' } as never, index)?.path).toBe(
      'Notes/工具/Modeling/Creo.md'
    )
  })

  it('matches by title', () => {
    const index = buildNoteLinkIndex(notes)
    expect(resolveWikiNote({ target: '微积分' } as never, index)?.path).toBe('Notes/数学/微积分.md')
  })

  it('is case insensitive', () => {
    const index = buildNoteLinkIndex(notes)
    expect(resolveWikiNote({ target: 'creo' } as never, index)).toBeDefined()
  })

  it('returns undefined for a note that does not exist', () => {
    const index = buildNoteLinkIndex(notes)
    expect(resolveWikiNote({ target: '不存在' } as never, index)).toBeUndefined()
  })

  it('keeps the first of two notes with the same name', () => {
    const index = buildNoteLinkIndex([
      { path: 'a/One.md', title: 'One' },
      { path: 'b/One.md', title: 'One' }
    ])
    expect(resolveWikiNote({ target: 'One' } as never, index)?.path).toBe('a/One.md')
  })
})
