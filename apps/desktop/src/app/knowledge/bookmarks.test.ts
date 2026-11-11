// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'

import { readBookmarks, toggleBookmark } from './bookmarks'

/**
 * 书签落在**本机**（localStorage），不进笔记仓——它是「我常翻这几篇」的人读面状态。
 */
describe('bookmarks', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('空的时候读回来是空的', () => {
    expect(readBookmarks()).toEqual([])
  })

  it('加一个书签，再读回来就是它', () => {
    toggleBookmark('Notes/数学/微积分.md')

    expect(readBookmarks()).toEqual(['Notes/数学/微积分.md'])
  })

  it('再加一个，两个都在（按加进去的顺序）', () => {
    toggleBookmark('Notes/数学/微积分.md')
    toggleBookmark('Notes/物理/大学物理.md')

    expect(readBookmarks()).toEqual(['Notes/数学/微积分.md', 'Notes/物理/大学物理.md'])
  })

  it('再按一次就是去掉', () => {
    toggleBookmark('Notes/数学/微积分.md')
    toggleBookmark('Notes/数学/微积分.md')

    expect(readBookmarks()).toEqual([])
  })

  it('存的不是数组就当没有（不抛）', () => {
    window.localStorage.setItem('plobi.notes.bookmarks', '不是数组')

    expect(readBookmarks()).toEqual([])
  })
})
