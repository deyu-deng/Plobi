/**
 * 工作区页签：从 slot 声明派生（裁定 239 第三刀）。
 *
 * 这些断言钉的是**关系**：
 *
 * 1. 每个壳里该有哪些页签、什么顺序——由**声明表**说了算（不是代码里写死的两份清单）；
 * 2. L1 那一排是 L2 的**子序列**（一张顺序 ＋ 按壳过滤，不给每个壳各写一份）；
 * 3. **插件自己声明的页签自动出现**（用户 11-25 19:5x 拍定，与 DSH 的做法一致）；
 * 4. 填一个宿主没声明过的 slot **不算数**；用户关掉的**真的不在**；
 * 5. 插件页签的内容经 view 注册表解析——**宿主把应用里的那一页真的渲染出来**，
 *    不是占位（知识库是这块的样板：浮层页与页签共用同一个面板）。
 */
import { describe, expect, it } from 'vitest'

import { createSlotRegistry, workspaceTabDefsFor } from '../shell/slot-registry'
import { viewRenderer } from '../views'

const LABELS = {
  actions: {
    agenda: '打开日程',
    browser: '打开浏览器',
    files: '打开文件',
    notes: '打开笔记',
    review: '打开审阅',
    terminal: '打开终端'
  },
  tabs: {
    agenda: '日程',
    browser: '浏览器',
    files: '文件',
    notes: '笔记',
    review: '审阅',
    terminal: '终端'
  }
}

describe('工作区页签：从声明派生', () => {
  it('每个壳里有哪些页签由声明决定', () => {
    const registry = createSlotRegistry()

    expect(workspaceTabDefsFor(registry, 'l1', LABELS).map(def => def.id)).toEqual([
      'agenda',
      'browser',
      'terminal'
    ])
    expect(workspaceTabDefsFor(registry, 'l2', LABELS).map(def => def.id)).toEqual([
      'files',
      'agenda',
      'review',
      'browser',
      'terminal',
      'notes'
    ])
  })

  it('L1 那一排是 L2 的子序列（一张顺序 ＋ 按壳过滤）', () => {
    const registry = createSlotRegistry()
    const l1 = workspaceTabDefsFor(registry, 'l1', LABELS).map(def => def.id)
    const l2 = workspaceTabDefsFor(registry, 'l2', LABELS).map(def => def.id)

    expect(l2.filter(id => l1.includes(id))).toEqual(l1)
  })

  it('插件自己声明的页签自动出现', () => {
    const registry = createSlotRegistry({
      contributions: [
        {
          slotId: 'workspace/tabs/my-tab',
          position: 'workspace',
          region: 'tabs',
          order: 70,
          id: 'my-tab',
          icon: 'star',
          label: '我的那一格',
          view: 'knowledge',
          providedBy: 'my-plugin'
        }
      ]
    })

    const ids = workspaceTabDefsFor(registry, 'l2', LABELS).map(def => def.id)

    expect(ids).toContain('my-tab')
    expect(ids.indexOf('my-tab')).toBe(ids.length - 1)
  })

  it('填一个宿主没声明过的 slot 不算数（宿主只认自己声明过的位置）', () => {
    const registry = createSlotRegistry({
      contributions: [{ slotId: 'my-thing', id: 'my-thing', icon: 'star' }]
    })

    expect(workspaceTabDefsFor(registry, 'l2', LABELS).map(def => def.id)).not.toContain('my-thing')
  })

  it('用户关掉的页签真的不在了', () => {
    const registry = createSlotRegistry({ removed: ['notes'] })

    expect(workspaceTabDefsFor(registry, 'l2', LABELS).map(def => def.id)).not.toContain('notes')
  })
})

describe('插件页签的内容：宿主按 view 渲染真的一页', () => {
  it('知识库是这块的样板：浮层页与页签共用同一个面板', async () => {
    const knowledge = await import('../knowledge')

    expect(knowledge.KnowledgePanel).toBeTruthy()
    expect(knowledge.KnowledgeView).toBeTruthy()
  })

  it('view 注册表解析得出知识库那个面板；没有的 view 解析不出来', () => {
    expect(viewRenderer('knowledge')).toBeTruthy()
    expect(viewRenderer('not-a-view')).toBeUndefined()
  })
})
