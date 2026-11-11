// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { NoteLinkGraph, NoteMeta } from './api'
import { NotePanels } from './panels'
import { buildNoteLinkIndex } from './wikilink'

// 每一例都渲染一次 ⇒ 不清场的话上一次的 DOM 还在，query 会撞车。
afterEach(() => {
  cleanup()
})

/**
 * 右栏那五块（WP-NOTES-PANELS）：Obsidian 的右栏工作区。
 *
 * 钉住五件事：
 * ① 反向链接＝**谁链到了这一篇**（不是这篇链出去的）
 * ② 出链＝这篇链出去的；**解析不上的原样留着**，不静默丢掉
 * ③ 标签＝全库的标签（带篇数）
 * ④ 属性＝这一篇的 frontmatter
 * ⑤ 书签＝收藏夹里的那几篇
 */
const NOTES: NoteMeta[] = [
  { path: 'Notes/数学/微积分.md', title: '微积分', topic: '数学', tags: ['数学', '极限'], belongs: [], updated: '', chars: 10, excerpt: '' },
  { path: 'Notes/物理/大学物理.md', title: '大学物理', topic: '物理', tags: ['物理'], belongs: [], updated: '', chars: 10, excerpt: '' }
]

const GRAPH: NoteLinkGraph = {
  backlinks: { 'Notes/物理/大学物理.md': ['Notes/数学/微积分.md'] },
  links: { 'Notes/数学/微积分.md': ['Notes/物理/大学物理.md'] },
  titles: { 'Notes/数学/微积分.md': '微积分', 'Notes/物理/大学物理.md': '大学物理' },
  totals: { links: 1, notes: 2 }
}

const TEXT = ['---', 'title: 大学物理', 'tags: [物理]', '---', '', '看 [[微积分]]，也看 [[没写的那篇]]。'].join('\n')

function renderPanels(overrides: Partial<Parameters<typeof NotePanels>[0]> = {}) {
  return render(
    <NotePanels
      bookmarks={[]}
      current={{ path: 'Notes/物理/大学物理.md', title: '大学物理' }}
      graph={GRAPH}
      linkIndex={buildNoteLinkIndex(NOTES)}
      notes={NOTES}
      onFilterTag={() => {}}
      onOpenNote={() => {}}
      onToggleBookmark={() => {}}
      text={TEXT}
      {...overrides}
    />
  )
}

describe('NotePanels', () => {
  it('反向链接列的是谁链到了这一篇', () => {
    renderPanels()

    // 「微积分」在两块里都出现（这篇链出去的 ＋ 链到这篇的）⇒ 按块取。
    const panel = within(screen.getByTestId('note-backlinks'))

    expect(panel.getByText('微积分')).toBeTruthy()
    expect(within(screen.getByTestId('note-outgoing')).getByText('微积分')).toBeTruthy()
  })

  it('出链解析不上的目标原样留着，不静默丢掉', () => {
    renderPanels()

    expect(within(screen.getByTestId('note-outgoing')).getByText('没写的那篇')).toBeTruthy()
  })

  it('标签列全库的标签', () => {
    renderPanels()

    const panel = within(screen.getByTestId('note-tags'))

    expect(panel.getByText('#数学')).toBeTruthy()
    expect(panel.getByText('#物理')).toBeTruthy()
  })

  it('属性列这一篇的 frontmatter', () => {
    renderPanels()

    const panel = within(screen.getByTestId('note-properties'))

    expect(panel.getByText('title')).toBeTruthy()
    expect(panel.getByText('大学物理')).toBeTruthy()
  })

  it('书签列收藏夹里的那几篇', () => {
    renderPanels({ bookmarks: ['Notes/数学/微积分.md'] })

    expect(within(screen.getByTestId('note-bookmarks')).getByText('微积分')).toBeTruthy()
  })

  it('点标签把标签报给上层（左栏据此筛）', () => {
    const onFilterTag = vi.fn()

    renderPanels({ onFilterTag })

    within(screen.getByTestId('note-tags')).getByText('#数学').click()

    expect(onFilterTag).toHaveBeenCalledWith('数学')
  })

  it('点反向链接里的那一篇能打开它', () => {
    const onOpenNote = vi.fn()

    renderPanels({ onOpenNote })

    within(screen.getByTestId('note-backlinks')).getByText('微积分').click()

    expect(onOpenNote).toHaveBeenCalledWith('Notes/数学/微积分.md')
  })
})
