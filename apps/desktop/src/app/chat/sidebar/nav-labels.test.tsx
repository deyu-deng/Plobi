import { describe, expect, it } from 'vitest'

import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'

import { DEFAULT_SLOT_DECLARATION } from '../../shell/slot-registry'

/**
 * 用户 2026-10-28 指着左栏那枚孤零零的铅笔：「日记这里怎么只有一个矢量符号」。
 * 成因是 `sidebar.nav` 里少了 `diary` 那一格，而渲染写的是 `s.nav[item.id] ?? item.label`——
 * 缺翻译就静默退回空串，屏上什么都不显示。这一条把这一类钉死：每一格都要有名字。
 *
 * **11-25 更新**：左栏那几格改从 **slot 声明表**派生（裁定 239），所以这一条改成查声明表
 * 里的侧边栏格子——**出厂那几格**每一格都要有名字（插件带来的格子用自己的标签，不进这张表）。
 * 只查 `entry-row` 家族：**`action` 家族**（「新建 Agent」那一枚独立的行动按钮）走的是
 * 自己的标签键（`sidebar.newAgentLabel`），不进这张 `sidebar.nav.*` 表。
 */
const SIDEBAR_SLOT_IDS = DEFAULT_SLOT_DECLARATION.filter(
  slot => slot.position === 'sidebar' && slot.region === 'top' && slot.kind === 'entry-row'
).map(slot => slot.id)

describe('左栏每一项都得有名字（zh 与 en 两语）', () => {
  it.each(SIDEBAR_SLOT_IDS.map(id => [id]))('%s 在两语里都有非空标签', id => {
    expect(zh.sidebar.nav[id], `zh 少了 sidebar.nav.${id}`).toBeTruthy()
    expect(en.sidebar.nav[id], `en 少了 sidebar.nav.${id}`).toBeTruthy()
  })
})
