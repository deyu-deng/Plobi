/**
 * slot 声明表与注册口（裁定 239 第一刀 · `WP-SHELL-SLOT-REGISTRY`）。
 *
 * 这些断言钉的是**关系**，不是今天的样子：
 *
 * 1. 声明表覆盖宿主留下的三个位置，每条 slot 的必填字段齐；
 * 2. **今天在屏上的那几段清单一条都不许漏**——声明表漏一条，就是那一格凭空消失；
 * 3. **左栏下面那排是另一种 slot 家族**（用户 11-25 22:2x：「我们侧边栏底部的图标是
 *    另一种slot」——12:2x 那句「上面和下面其实差不多」说的是**形状**，不是同一族）；
 *    中间那一大块又是另一族；
 * 4. **声明表可以被用户整份替换**（裁定 238 补记 1 的"整份 override"在界面上的落点）；
 * 5. **默认插件可以被用户关掉**，关掉后那一格**真的没有内容**（不是藏起来）；
 * 6. 插件填进来的东西按 slot 覆盖出厂那一份，且同样受用户那句"关掉"约束。
 */
import { describe, expect, it } from 'vitest'

import {
  createSlotRegistry,
  DEFAULT_SLOT_DECLARATION,
  settingsSectionsFor,
  type SlotContribution,
  type SlotDeclaration
} from './slot-registry'

/** 今天屏上那几段清单（这是契约，不是快照：少一条就是少一格）。 */
const SCREEN_SIDEBAR_TOP = ['skills', 'messaging', 'artifacts', 'knowledge', 'diary']
const SCREEN_SIDEBAR_BOTTOM = ['system:settings', 'agents', 'cron', 'agenda', 'gateway-health', 'egress']
const SCREEN_CHAT_CARD = ['dossier', 'pending', 'mind-pending']
const SCREEN_WORKSPACE = ['agenda', 'browser', 'files', 'notes', 'review', 'terminal']
/** 设置屏那八段（用户 11-25 23:3x：设置屏＝第四个位置，一段＝一组配置字段）。 */
const SCREEN_SETTINGS_SECTIONS = [
  'model',
  'chat',
  'appearance',
  'workspace',
  'safety',
  'memory',
  'voice',
  'advanced'
]

describe('slot 声明表', () => {
  it('覆盖宿主留下的三个位置 ＋ 设置屏那一个（**外壳仍是三个**）', () => {
    const positions = new Set(DEFAULT_SLOT_DECLARATION.map(slot => slot.position))

    expect([...positions].sort()).toEqual(['chat-card', 'settings', 'sidebar', 'workspace'])
  })

  it('每条 slot 的必填字段齐', () => {
    for (const slot of DEFAULT_SLOT_DECLARATION) {
      expect(slot.id, 'slot 缺 id').toBeTruthy()
      expect(slot.position, `${slot.id} 缺 position`).toBeTruthy()
      expect(slot.region, `${slot.id} 缺 region`).toBeTruthy()
      expect(['action', 'entry-row', 'panel', 'status-item', 'settings-section'], `${slot.id} 的 kind 不认识`).toContain(
        slot.kind
      )
      expect(Number.isFinite(slot.order), `${slot.id} 缺 order`).toBe(true)
      expect(slot.shape, `${slot.id} 缺 shape`).toBeTruthy()
      expect(typeof slot.shape.icon, `${slot.id} 的 shape 缺 icon`).toBe('boolean')
      expect(typeof slot.shape.label, `${slot.id} 的 shape 缺 label`).toBe('boolean')
      expect(slot.props, `${slot.id} 缺 props 契约`).toBeTruthy()
    }
  })

  it('今天屏上那几段清单一条都不许漏', () => {
    const idsOf = (position: string, region: string) =>
      DEFAULT_SLOT_DECLARATION.filter(slot => slot.position === position && slot.region === region).map(slot => slot.id)

    expect(idsOf('sidebar', 'top')).toEqual(expect.arrayContaining(SCREEN_SIDEBAR_TOP))
    expect(idsOf('sidebar', 'bottom')).toEqual(expect.arrayContaining(SCREEN_SIDEBAR_BOTTOM))
    expect(idsOf('chat-card', 'top-right')).toEqual(expect.arrayContaining(SCREEN_CHAT_CARD))
    expect(idsOf('workspace', 'tabs')).toEqual(expect.arrayContaining(SCREEN_WORKSPACE))
    expect(idsOf('settings', 'sections')).toEqual(expect.arrayContaining(SCREEN_SETTINGS_SECTIONS))
  })

  it('左栏**下面那排是另一种 slot 家族**，中间那一大块又是另一族（用户 11-25 22:2x）', () => {
    // 11-25 12:2x 说「上面和下面其实差不多」——那说的是**形状**（都是一枚小图标按钮）。
    // 11-25 22:2x 纠正：**「我们侧边栏底部的图标是另一种slot」**——宿主管的东西不同
    // （上面是导航，下面是状态栏那一件）。
    const kindOf = (region: string) =>
      DEFAULT_SLOT_DECLARATION.find(slot => slot.position === 'sidebar' && slot.region === region)?.kind

    expect(kindOf('top')).toBe('entry-row')
    expect(kindOf('bottom')).toBe('status-item')
    expect(kindOf('middle')).toBe('panel')
  })

  it('「新建 Agent」是 action 家族——与导航格子**不是同一种组件**', () => {
    // 用户 11-25 19:4x：「新建 Agent 可以像 DSH 一样用不同的组件区分开来」。
    const action = DEFAULT_SLOT_DECLARATION.find(
      slot => slot.position === 'sidebar' && slot.region === 'top' && slot.kind === 'action'
    )
    const nav = DEFAULT_SLOT_DECLARATION.find(
      slot => slot.position === 'sidebar' && slot.region === 'top' && slot.id === 'skills'
    )

    expect(action, '声明表里没有那个独立的行动按钮').toBeTruthy()
    expect(action?.id).toBe('new-agent')
    expect(nav?.kind).toBe('entry-row')
    // 它排在导航那几格**之前**（DSH 那个「New Session」就在上面）
    expect(action!.order).toBeLessThan(nav!.order)
  })

  it('每个区域的 slot 按 order 排', () => {
    const registry = createSlotRegistry()

    for (const position of ['sidebar', 'chat-card', 'workspace'] as const) {
      const orders = registry.slotsFor(position).map(slot => slot.order)
      expect(orders, `${position} 的顺序不是递增的`).toEqual([...orders].sort((a, b) => a - b))
    }
  })
})

describe('声明表可以被用户整份替换', () => {
  it('换一份声明，宿主渲染的就是那一份', () => {
    const mine: SlotDeclaration[] = [
      {
        id: 'my-thing',
        position: 'sidebar',
        region: 'top',
        kind: 'entry-row',
        order: 10,
        shape: { icon: true, label: true },
        props: {},
        default: { id: 'my-thing', icon: 'star', label: '我的那一格', providedBy: 'builtin' }
      }
    ]

    const registry = createSlotRegistry({ declaration: mine })

    expect(registry.slotsFor('sidebar', 'top').map(slot => slot.id)).toEqual(['my-thing'])
    expect(registry.contentFor('sidebar', 'top', 'my-thing')?.label).toBe('我的那一格')
    // 出厂那几格不在这一份声明里 ⇒ 那一格真的不在了
    expect(registry.contentFor('sidebar', 'top', 'diary')).toBeUndefined()
  })
})

describe('默认插件可以被用户关掉', () => {
  it('关掉之后那一格真的没有内容', () => {
    const registry = createSlotRegistry({ removed: ['sidebar/top/diary'] })

    expect(registry.contentFor('sidebar', 'top', 'diary')).toBeUndefined()
    // 别的格子不受影响
    expect(registry.contentFor('sidebar', 'top', 'skills')).toBeTruthy()
    // 关掉的是"那一格"，不是"那一个位置"
    expect(registry.slotsFor('sidebar', 'top').map(slot => slot.id)).toContain('diary')
  })

  it('**关掉一处不影响另一处**：同一个短名在两块区域里各有一个（用户 11-25 22:2x：「两边都留着…允许插件往多个地方插入内容」）', () => {
    const registry = createSlotRegistry({ removed: ['workspace/tabs/agenda'] })

    // 关掉的是**工作区那个日程页签**
    expect(registry.contentFor('workspace', 'tabs', 'agenda')).toBeUndefined()
    // 左栏下面那枚日程**还在**（它是另一份 slot，身份不同）
    expect(registry.contentFor('sidebar', 'bottom', 'agenda')).toBeTruthy()
  })

  it('插件贡献的内容同样受用户那句"关掉"约束', () => {
    const registry = createSlotRegistry({
      contributions: [{ slotId: 'skills', id: 'skills', icon: 'star', label: '插件换的那一格' }],
      removed: ['sidebar/top/skills']
    })

    expect(registry.contentFor('sidebar', 'top', 'skills')).toBeUndefined()
  })
})

describe('插件填进来的东西', () => {
  it('按 slot 覆盖出厂那一份', () => {
    const registry = createSlotRegistry({
      contributions: [{ slotId: 'skills', id: 'skills', icon: 'star', label: '插件换的那一格', providedBy: 'some-plugin' }]
    })

    expect(registry.contentFor('sidebar', 'top', 'skills')?.label).toBe('插件换的那一格')
    expect(registry.contentFor('sidebar', 'top', 'skills')?.providedBy).toBe('some-plugin')
  })

  it('填到一个没有声明的 slot 上不算数（宿主不认没声明过的位置）', () => {
    const registry = createSlotRegistry({
      contributions: [{ slotId: 'not-declared', id: 'not-declared', icon: 'star', label: '没声明过的那一格' }]
    })

    expect(registry.contentFor('sidebar', 'top', 'not-declared')).toBeUndefined()
  })

  it('设置屏那一族：插件加的一段**自动出现**，里面是它声明的那几个字段', () => {
    // 用户 11-25 23:3x：设置屏＝第四个位置，一段＝一组配置字段。
    const contribution: SlotContribution = {
      slotId: 'settings/sections/my-section',
      position: 'settings',
      region: 'sections',
      kind: 'settings-section',
      id: 'my-section',
      label: '我的一段',
      keys: ['plugins.entries.my-thing.api_key']
    }
    const registry = createSlotRegistry({ contributions: [contribution] })

    const sections = settingsSectionsFor(registry)

    expect(sections.map(section => section.id)).toContain('my-section')
    const mine = sections.find(section => section.id === 'my-section')
    expect(mine?.label).toBe('我的一段')
    expect(mine?.keys).toEqual(['plugins.entries.my-thing.api_key'])
  })

  it('设置屏那一族：出厂那八段**由声明表说了算**（顺序、名字、字段清单）', () => {
    const sections = settingsSectionsFor(createSlotRegistry())

    expect(sections.map(section => section.id)).toEqual([
      'model',
      'chat',
      'appearance',
      'workspace',
      'safety',
      'memory',
      'voice',
      'advanced'
    ])
    // 一段＝一组配置字段（记忆那一段带四组分组）。
    const memory = sections.find(section => section.id === 'memory')
    expect(memory?.keys).toContain('memory.provider')
    expect(memory?.groups?.map(group => group.titleKey)).toEqual([
      'settings.groups.memory.working',
      'settings.groups.memory.longTerm',
      'settings.groups.memory.mind',
      'settings.groups.memory.external'
    ])
  })

  it('设置屏那一族：用户关掉的那一段**真的不在**', () => {
    const sections = settingsSectionsFor(createSlotRegistry({ removed: ['settings/sections/voice'] }))

    expect(sections.map(section => section.id)).not.toContain('voice')
    expect(sections.map(section => section.id)).toContain('model')
  })
})
