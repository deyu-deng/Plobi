/**
 * 命令面板第一组「跳到某一处」：**从 slot 声明表派生**（用户 11-27 22:5x）。
 *
 * 这一列**以前是一份手写清单**（11 条，还漏了日程）⇒ 声明表加一格，命令面板不会知道。
 * 判据是**关系式**的：不写死有几条，而是问「这一列由谁说了算」。
 *
 * 规矩三条（与 `jumpTargetsFor` 同一份口径）：
 * 1. **顺序＝声明顺序**；
 * 2. **落点去重**（同一个落点只留一条）；
 * 3. **没有落点的不要**（工作区那几页签、对话卡那三枚是**面板**，不是页）。
 */
import { describe, expect, it } from 'vitest'

import { zh } from '@/i18n/zh'

import { resolveSlotLabel } from '../shell/slot-labels'
import { createSlotRegistry, jumpTargetsFor, type SlotContribution } from '../shell/slot-registry'

/** 插件往左栏上面那排加的一格（声明式：插件**不在界面上执行代码**）。 */
const PLUGIN_TARGET: SlotContribution = {
  slotId: 'sidebar/top/my-thing',
  position: 'sidebar',
  region: 'top',
  kind: 'entry-row',
  id: 'my-thing',
  icon: 'star',
  label: '我的一格',
  route: '/my-thing',
  keywords: ['my thing', 'my-words']
}

function targets(contributions: SlotContribution[] = [], removed: string[] = []) {
  return jumpTargetsFor(createSlotRegistry({ contributions, removed }))
}

describe('命令面板「跳到某一处」：清单由声明表说了算', () => {
  it('出厂那几页都在，顺序照声明', () => {
    expect(targets().map(item => item.id)).toEqual([
      'skills',
      'messaging',
      'artifacts',
      'knowledge',
      'diary',
      'agenda',
      'cron',
      'command-center',
      'settings',
      'starmap',
      'agents'
    ])
  })

  it('**落点去重**：同一个落点只留一条（左栏底部那两枚指向的页，上面那排已经有了）', () => {
    const ids = targets().map(item => item.id)

    // 「当前这个 Agent 的技能与工具」→ /skills；上面那排的「技能与工具」也是 /skills。
    expect(ids).not.toContain('capabilities')
    expect(ids).not.toContain('agent-artifacts')
    // 每一项的落点都不一样。
    const routes = targets().map(item => item.route)

    expect(new Set(routes).size).toBe(routes.length)
  })

  it('**插件加的那一格自动出现在这一列里**（带自己的名字与搜索词）', () => {
    const items = targets([PLUGIN_TARGET])

    expect(items.map(item => item.id)).toContain('my-thing')
    const mine = items.find(item => item.id === 'my-thing')
    expect(mine?.label).toBe('我的一格')
    expect(mine?.route).toBe('/my-thing')
    // 搜索词是**声明说了算**（插件自己声明它那几个词 ⇒ 它那一格搜得到）。
    expect(mine?.keywords).toEqual(['my thing', 'my-words'])
  })

  it('用户关掉的那一格**不在这一列里**', () => {
    const ids = targets([], ['sidebar/top/knowledge']).map(item => item.id)

    expect(ids).not.toContain('knowledge')
    expect(ids).toContain('diary')
  })

  it('**没有落点的那几格不在这一列**（工作区那几页签、对话卡那三枚是面板，不是页）', () => {
    const ids = targets().map(item => item.id)

    expect(ids).not.toContain('dossier')
    expect(ids).not.toContain('pending')
    expect(ids).not.toContain('files')
    expect(ids).not.toContain('notes')
  })

  it('**记忆图谱**：左栏上面那排有它（用户 11-27 22:5x 拍板），这一列里也有它', () => {
    const registry = createSlotRegistry()
    const starmap = registry.contentFor('sidebar', 'top', 'starmap')

    expect(starmap, '记忆图谱不在左栏上面那排').toBeTruthy()
    expect(starmap?.route).toBe('/starmap')
    // 名字走 i18n 的**点号路径**（四语各有自己那句）。
    expect(resolveSlotLabel(zh, starmap?.labelKey)).toBe('记忆图谱')
    expect(targets().map(item => item.id)).toContain('starmap')
  })

  it('快捷键与搜索词**跟着内容走**（不是另一份写死的清单）', () => {
    const skills = targets().find(item => item.id === 'skills')

    expect(skills?.action).toBe('nav.skills')
    expect(skills?.keywords).toContain('mcp')
  })
})
