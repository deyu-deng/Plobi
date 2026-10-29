import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'

import { SIDEBAR_NAV } from './index'

/**
 * 用户 2026-10-28 指着左栏那枚孤零零的铅笔：「日记这里怎么只有一个矢量符号」。
 * 成因是 `sidebar.nav` 里少了 `diary` 那一格，而渲染写的是 `s.nav[item.id] ?? item.label`——
 * 缺翻译就静默退回空串，屏上什么都不显示。这一条把这一类钉死：每一格都要有名字。
 */
describe('左栏每一项都得有名字（zh 与 en 两语）', () => {
  it.each(SIDEBAR_NAV.map(item => [item.id]))('%s 在两语里都有非空标签', id => {
    expect(zh.sidebar.nav[id], `zh 少了 sidebar.nav.${id}`).toBeTruthy()
    expect(en.sidebar.nav[id], `en 少了 sidebar.nav.${id}`).toBeTruthy()
  })
})
