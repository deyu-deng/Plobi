/**
 * 输入框右下角**那一排**从 slot 声明表派生（用户 11-27 22:0x 点的第②块）。
 *
 * 一格＝**一个控件位**：宿主自己的控件由宿主按 id 渲染（那几枚的悬停文案**跟着状态变**，
 * 不是声明里的死字）；插件加的那一格＝**图标＋名字＋去哪** ⇒ 点它跳过去。
 *
 * 判据是**关系式**的：不写死有几枚，而是问「那一排由谁说了算」。
 */
import { describe, expect, it } from 'vitest'

import { composerControlsFor, createSlotRegistry, type SlotContribution } from '../../shell/slot-registry'

/** 插件加的那一格（声明式：插件**不在界面上执行代码**）。 */
const PLUGIN_CONTROL: SlotContribution = {
  slotId: 'composer/controls/my-control',
  position: 'composer',
  region: 'controls',
  kind: 'entry-row',
  order: 50,
  id: 'my-control',
  icon: 'star',
  label: '我的一枚',
  route: '/knowledge'
}

function controls(contributions: SlotContribution[] = [], removed: string[] = []) {
  return composerControlsFor(createSlotRegistry({ contributions, removed }))
}

describe('输入框右下角那一排', () => {
  it('出厂那几格都在，顺序照声明，**主按钮排最后**（它是「发送」那一枚）', () => {
    expect(controls().map(item => item.id)).toEqual(['model', 'dictation', 'auto-speak', 'primary'])
  })

  it('宿主那几格带的是**控件 id**（声明里只有数据、没有组件）', () => {
    const byId = new Map(controls().map(item => [item.id, item]))

    expect(byId.get('model')?.control).toBe('model-pill')
    expect(byId.get('dictation')?.control).toBe('dictation')
    expect(byId.get('auto-speak')?.control).toBe('auto-speak')
    expect(byId.get('primary')?.control).toBe('primary')
  })

  it('**插件加的那一格自动出现**（落在主按钮前面），带图标＋名字＋去哪', () => {
    const items = controls([PLUGIN_CONTROL])

    expect(items.map(item => item.id)).toEqual(['model', 'dictation', 'auto-speak', 'my-control', 'primary'])
    const mine = items.find(item => item.id === 'my-control')
    expect(mine?.icon).toBe('star')
    expect(mine?.label).toBe('我的一枚')
    expect(mine?.route).toBe('/knowledge')
    // 插件那一格没有宿主的控件 id（它自己带内容）。
    expect(mine?.control).toBeUndefined()
  })

  it('用户关掉的那一格**真的不在**', () => {
    const items = controls([], ['composer/controls/auto-speak'])

    expect(items.map(item => item.id)).not.toContain('auto-speak')
    expect(items.map(item => item.id)).toContain('primary')
  })
})
