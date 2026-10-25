/**
 * 折叠那一格自己的契约（三态 / 落点键名 / 只存明说过的 / 认不出的值不猜）。
 *
 * 界面上"点得动、行不画"那一半在 `../sources-settings.test.tsx`；这一档钉的是**形状**，
 * 因为它一旦变形就会以别的样子坏掉：把"当前开着的段"整表写进去 ⇒ 删一条信源留下垃圾键；
 * 把别的词猜成 open/collapsed ⇒ 他折好的段在某次读盘之后自己弹开。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $fold, decide, isOpen } from './collapse'

const KEY = 'plobi.desktop.settings.sources.fold'

beforeEach(() => {
  $fold.set({})
})

afterEach(() => {
  window.localStorage.removeItem(KEY)
})

describe('isOpen', () => {
  it('lets his own word beat the readings', () => {
    expect(isOpen({ message: 'collapsed' }, { category: 'message', errors: 3 })).toBe(false)
    expect(isOpen({ email: 'open' }, { category: 'email', errors: 0 })).toBe(true)
  })

  it('keeps the group that holds an action open even with nothing in it', () => {
    // 「接一个自己的」那段 0 行是常态；按自动规则它会被折没，而那颗入口正是那一格的唯一内容。
    expect(isOpen({}, { category: 'custom', errors: 0 })).toBe(true)
  })

  it('otherwise follows the readings: something broken opens, nothing broken folds', () => {
    expect(isOpen({}, { category: 'website', errors: 2 })).toBe(true)
    expect(isOpen({}, { category: 'website', errors: 0 })).toBe(false)
  })
})

describe('what lands in storage', () => {
  it('stores only the category he decided, under the app-prefixed key', () => {
    decide('message', false)

    expect(JSON.parse(window.localStorage.getItem(KEY) ?? 'null')).toEqual({ message: 'collapsed' })

    decide('website', true)

    expect(JSON.parse(window.localStorage.getItem(KEY) ?? 'null')).toEqual({ message: 'collapsed', website: 'open' })
  })

  it('drops the key entirely when nothing was ever decided', () => {
    // 空表编成 null ⇒ storage 那道闸把键删掉，而不是留一个 `{}`：
    // "清掉这一个键"因此等于回到自动规则，而不是一堆 `open` 的残留。
    decide('message', false)
    $fold.set({})

    expect(window.localStorage.getItem(KEY)).toBeNull()
  })
})

describe('a value this version does not know', () => {
  it('reads as "he never said", not as a guess', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ message: 'sideways', website: 42 }))
    vi.resetModules()

    const fresh = await import('./collapse')

    expect(fresh.$fold.get()).toEqual({})
    expect(fresh.isOpen(fresh.$fold.get(), { category: 'message', errors: 0 })).toBe(false)
  })

  it('survives a value that is not even an object', async () => {
    window.localStorage.setItem(KEY, '[1,2,3]')
    vi.resetModules()

    const fresh = await import('./collapse')

    expect(fresh.$fold.get()).toEqual({})
  })
})
