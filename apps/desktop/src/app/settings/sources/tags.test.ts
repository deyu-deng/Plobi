/**
 * 那一排标签 chip 的判据本体（`./tags`）——与 `collapse.test.ts` 同一族：判据在纯函数里，
 * 屏上那一排的行为另有整屏测试（`../sources-settings.test.tsx` 里那五条）。
 *
 * 这里钉的是**最贵的那两处跑偏**：
 * ① 标签的真源只能是池里那条 `kind`（不许出现第二份形状表）；
 * ② 筛选是浏览决定——空表要在存储里**不占一格**，坏形状不当"他选过"。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $tags, keepsRow, tagOptions, toggleTag } from './tags'

const KEY = 'plobi.desktop.settings.sources.tags'

beforeEach(() => {
  window.localStorage.clear()
  $tags.set([])
})

describe('the chips take their values from the pool alone', () => {
  it('lists each shape once, biggest first, ties by name', () => {
    const options = tagOptions([
      { pool_kinds: { doc: 42, message: 4 } },
      { pool_kinds: { message: 244 } },
      { pool_kinds: { class: 65, ddl: 64 } }
    ])

    expect(options).toEqual(['message', 'class', 'ddl', 'doc'])
  })

  it('does not invent a shape the pool never had', () => {
    expect(tagOptions([{ pool_kinds: {} }, {}])).toEqual([])
  })

  it('is the only source of the list — no second table of shapes', () => {
    // 有人想给一行加个 `tags: [...]` 字段时，这一条会先红：chip 的取值只认 `pool_kinds`。
    expect(tagOptions([{ other_field: ['doc'] } as never])).toEqual([])
  })
})

describe('what a pick keeps', () => {
  it('nothing picked means nothing filtered', () => {
    expect(keepsRow({ pool_kinds: { doc: 1 } }, [])).toBe(true)
    expect(keepsRow({}, [])).toBe(true)
  })

  it('keeps a row that carries any one of the picked shapes', () => {
    const row = { pool_kinds: { doc: 3, message: 9 } }

    expect(keepsRow(row, ['doc'])).toBe(true)
    expect(keepsRow(row, ['mail', 'message'])).toBe(true)
    expect(keepsRow(row, ['mail'])).toBe(false)
  })

  it('a source that has never landed anything is not shown under a pick', () => {
    // 这不是惩罚它，而是"这一条在池里没有带那个形状的条目"的真话——它仍然在没筛的时候看得见。
    expect(keepsRow({ pool_kinds: {} }, ['doc'])).toBe(false)
  })
})

describe('the pick is a browsing decision', () => {
  it('adds and removes one word at a time', () => {
    expect(toggleTag([], 'doc')).toEqual(['doc'])
    expect(toggleTag(['doc', 'message'], 'doc')).toEqual(['message'])
    expect(toggleTag(['doc'], 'event')).toEqual(['doc', 'event'])
  })

  it('lands in localStorage and an empty selection leaves no cell behind', () => {
    $tags.set(['doc'])

    expect(JSON.parse(window.localStorage.getItem(KEY) ?? 'null')).toEqual(['doc'])

    $tags.set([])

    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('drops the words this version does not know instead of guessing', async () => {
    window.localStorage.setItem(KEY, JSON.stringify(['doc', 42, null, { a: 1 }]))
    vi.resetModules()

    const fresh = await import('./tags')

    expect(fresh.$tags.get()).toEqual(['doc'])
  })

  it('survives a stored value that is not even a list', async () => {
    window.localStorage.setItem(KEY, '[1,2,3]')
    vi.resetModules()

    const fresh = await import('./tags')

    expect(fresh.$tags.get()).toEqual([])
  })
})
