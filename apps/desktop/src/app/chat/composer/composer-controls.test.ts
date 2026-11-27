/**
 * 输入框右下角**那一排**从 slot 声明表派生（用户 11-27 22:0x 点的第②块）。
 *
 * 一格＝**一个控件位**：宿主自己的控件由宿主按 id 渲染（那几枚的悬停文案**跟着状态变**，
 * 不是声明里的死字）；插件加的那一格＝**图标＋名字＋去哪** ⇒ 点它跳过去。
 *
 * 判据是**关系式**的：不写死有几枚，而是问「那一排由谁说了算」。
 */
import { describe, expect, it } from 'vitest'

import {
  composerControlTarget,
  composerControlsFor,
  createSlotRegistry,
  type SlotContribution
} from '../../shell/slot-registry'

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

describe('插件加的那一格点开去哪儿', () => {
  /** 声明了 `view`（应用里那一页）的那一格。 */
  const VIEW_CONTROL: SlotContribution = {
    slotId: 'composer/controls/my-view',
    position: 'composer',
    region: 'controls',
    kind: 'entry-row',
    id: 'my-view',
    icon: 'star',
    label: '我的一页',
    view: 'knowledge'
  }

  /** 从那一排里**按 id**取一格（出厂那几格排在前面，不能取第一个）。 */
  const pick = (contributions: SlotContribution[], id: string) =>
    controls(contributions).find(item => item.id === id)

  it('声明 `view` ⇒ 落在**应用里那一页**（页名 → 那页的路）', () => {
    const item = pick([VIEW_CONTROL], 'my-view')

    expect(item?.view).toBe('knowledge')
    expect(composerControlTarget(item!)).toBe('/knowledge')
  })

  it('给了 `route` 就走它（**左栏那排也是这条路**）', () => {
    const item = pick([{ ...VIEW_CONTROL, route: '/my-own-page' }], 'my-view')

    expect(composerControlTarget(item!)).toBe('/my-own-page')
  })

  it('**没有落点的那一格不画**（不留一个点了没反应的死按钮）', () => {
    const item = pick(
      [
        {
          slotId: 'composer/controls/my-nothing',
          position: 'composer',
          region: 'controls',
          kind: 'entry-row',
          id: 'my-nothing',
          icon: 'star',
          label: '没落点'
        }
      ],
      'my-nothing'
    )

    expect(composerControlTarget(item!)).toBeUndefined()
  })

  it('宿主自己那几格**没有落点**（它们是控件，不是入口）', () => {
    for (const item of controls()) {
      expect(composerControlTarget(item), `${item.id} 不该有落点`).toBeUndefined()
    }
  })
})
