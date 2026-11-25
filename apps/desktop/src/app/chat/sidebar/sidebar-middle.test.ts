/**
 * 左栏中间那一大块（**名单 ↔ 外包**）从 slot 声明表派生（裁定 239 路线第 6 刀）。
 *
 * **为什么这一刀现在做**：声明里**早就有这一族**（刀 2 落的），屏上却还是**手写的两行**
 * ⇒ 声明与屏对不上，那正是用户反复要的那种过渡态。**要么读它，要么把它从声明里删掉**
 * ——这一刀选读它。
 *
 * 判据是**关系式**的：不写死几条，而是问「这一族的清单由谁说了算」。
 */
import { describe, expect, it } from 'vitest'

import { zh } from '@/i18n/zh'

import { createSlotRegistry, sidebarMiddleItems, type SlotContribution } from '../../shell/slot-registry'

/** 插件往这一族里加的一格（声明式：插件**不在界面上执行代码**）。 */
const PLUGIN_TAB: SlotContribution = {
  slotId: 'sidebar/middle/my-tab',
  position: 'sidebar',
  region: 'middle',
  kind: 'panel',
  order: 30,
  id: 'my-tab',
  icon: 'star',
  label: '我加的那一格',
  view: 'knowledge'
}

function middleOf(contributions: SlotContribution[] = [], removed: string[] = []) {
  return sidebarMiddleItems(createSlotRegistry({ contributions, removed }), zh)
}

describe('左栏中段：清单由声明表说了算', () => {
  it('出厂那两格都在，顺序照声明', () => {
    expect(middleOf().map(item => item.id)).toEqual(['agents', 'outsourced'])
  })

  it('名字走 i18n 的**点号路径**（声明里的 labelKey 对着宿主那棵 i18n 树）', () => {
    const items = middleOf()

    expect(items[0]?.label).toBe(zh.console.tabAgents)
    expect(items[1]?.label).toBe(zh.console.tabOutsourced)
  })

  it('**插件加的那一格自动出现**（用户 11-25 22:2x：「允许插件往多个地方插入内容」）', () => {
    const items = middleOf([PLUGIN_TAB])

    expect(items.map(item => item.id)).toContain('my-tab')
    const mine = items.find(item => item.id === 'my-tab')
    expect(mine?.label).toBe('我加的那一格')
    // 它显示应用的哪一页也是声明说了算（宿主据此把那一页真的渲染出来）。
    expect(mine?.view).toBe('knowledge')
  })

  it('填一个宿主没声明过的 slot **不算数**', () => {
    const items = middleOf([{ slotId: 'sidebar/middle/nope', id: 'nope', label: '填错了' }])

    expect(items.map(item => item.id)).not.toContain('nope')
  })

  it('用户关掉的那一格**真的不在**（记的是 slot 的身份，不是短名）', () => {
    const items = middleOf([], ['sidebar/middle/outsourced'])

    expect(items.map(item => item.id)).not.toContain('outsourced')
    expect(items.map(item => item.id)).toContain('agents')
  })
})
