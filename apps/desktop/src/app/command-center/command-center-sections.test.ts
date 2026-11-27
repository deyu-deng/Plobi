/**
 * 命令中心那四段：**从 slot 声明表派生**（裁定 239 路线第 11 刀）。
 *
 * 用户 11-27 12:5x 在「一块块定义界面」那一轮里点的下一块：命令中心那四段
 * （会话／系统／用量／维护）今天写死在 `command-center/index.tsx` 里。
 *
 * 这一档钉三件事：
 * 1. 那四段**由声明表说了算**（顺序、名字）；
 * 2. **插件加的一段自动出现**，里面是**它声明的 `view`**（宿主把应用里那一页真的渲染出来）；
 * 3. 「一段」是**同一族**——设置屏那一段与命令中心那一段**形状相同**
 *    （一枚导航项＋一块内容）⇒ **不为每一段另立家族**。
 */
import { describe, expect, it } from 'vitest'

import { zh } from '@/i18n/zh'

import { createSlotRegistry, sectionsFor, type SlotContribution } from '../shell/slot-registry'

/** 插件加的一段（声明式：插件**不在界面上执行代码**）。 */
const PLUGIN_SECTION: SlotContribution = {
  slotId: 'command-center/sections/my-section',
  position: 'command-center',
  region: 'sections',
  kind: 'section',
  order: 50,
  id: 'my-section',
  label: '我的一段',
  view: 'knowledge'
}

function commandCenterSections(contributions: SlotContribution[] = []) {
  return sectionsFor(createSlotRegistry({ contributions }), 'command-center')
}

describe('命令中心那几段', () => {
  it('出厂那四段都在，顺序照声明', () => {
    expect(commandCenterSections().map(item => item.id)).toEqual([
      'sessions',
      'system',
      'usage',
      'maintenance'
    ])
  })

  it('名字走 i18n 的**点号路径**（声明里的 labelKey）', () => {
    const sessions = commandCenterSections().find(item => item.id === 'sessions')

    expect(sessions?.labelKey).toBe('commandCenter.sections.sessions')
  })

  it('**插件加的一段自动出现**，里面是它声明的 view（用户 11-27 12:5x）', () => {
    const sections = commandCenterSections([PLUGIN_SECTION])

    expect(sections.map(item => item.id)).toContain('my-section')
    const mine = sections.find(item => item.id === 'my-section')
    expect(mine?.label).toBe('我的一段')
    expect(mine?.view).toBe('knowledge')
  })

  it('用户关掉的那一段**真的不在**（按 slot 的身份记）', () => {
    const sections = sectionsFor(
      createSlotRegistry({ removed: ['command-center/sections/usage'] }),
      'command-center'
    )

    expect(sections.map(item => item.id)).not.toContain('usage')
    expect(sections.map(item => item.id)).toContain('sessions')
  })

  it('**「一段」是同一族**：设置屏那一段与命令中心那一段形状相同', () => {
    const registry = createSlotRegistry()
    const settings = sectionsFor(registry, 'settings')
    const commandCenter = sectionsFor(registry, 'command-center')

    // 同样的形状：一枚导航项＋一块内容（id／名字／图标由宿主给，内容各是各的）。
    expect(settings.length).toBeGreaterThan(0)
    expect(commandCenter.length).toBeGreaterThan(0)
    expect(Object.keys(settings[0]).sort()).toEqual(Object.keys(commandCenter[0]).sort())
    // 而「一段」显示什么（配置字段 vs 一块内容）是**内容**的事，不是家族的事。
    expect(settings[0].keys).toBeTruthy()
    expect(commandCenter[0].keys).toBeUndefined()
  })
})
