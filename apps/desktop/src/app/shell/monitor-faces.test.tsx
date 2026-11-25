// @vitest-environment jsdom
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { zh } from '@/i18n/zh'
import { $mindPending } from '@/store/mind-pending'
import { $pendingInbox, type PendingInboxItem } from '@/store/pending-inbox'

import { useMonitorFaceMetas, type MonitorFaceMeta } from './monitor-faces'
import { chatCardFaceDefsFor, createSlotRegistry, type SlotContribution } from './slot-registry'

/**
 * 对话卡右上角那一列：**从 slot 声明表派生**（裁定 239 第四刀）。
 *
 * 用户 11-25 19:5x：「**工作区的页签也是一个个插件。用户可以添加的**」——
 * 那一列也一样：插件带来的一枚**自动出现**，用户关掉的一枚**真的不在**。
 *
 * 判据是**关系式**的：不写死几条，而是问「清单由谁说了算」。
 */

/** 插件自己声明的一枚（声明式：插件**不在界面上执行代码**）。 */
const PLUGIN_FACE: SlotContribution = {
  slotId: 'chat-card/top-right/my-face',
  position: 'chat-card',
  region: 'top-right',
  kind: 'entry-row',
  order: 40,
  id: 'my-face',
  icon: 'star',
  label: '我的那一枚',
  view: 'knowledge',
  shells: ['l1', 'l2']
}

function facesOf(contributions: SlotContribution[] = [], removed: string[] = []): MonitorFaceMeta[] {
  return chatCardFaceDefsFor(createSlotRegistry({ contributions, removed }), zh)
}

/** 收件箱里的一件（票）。 */
function ticket(id: string, goal: string): PendingInboxItem {
  return {
    kind: 'ticket',
    id,
    goal,
    leg: 'router',
    status: 'active',
    state: 'ready',
    note: '',
    who: '',
    at_ms: 0,
    refs: {},
    triggered_by: 'webhook',
    created_at: 0,
    parent_agent: '',
    policy_version: '',
    revoke_reason: '',
    scope: {},
    updated_at: 0
  }
}

function FaceList() {
  const faces = useMonitorFaceMetas()

  return <div data-testid="faces">{JSON.stringify(faces)}</div>
}

describe('对话卡那一列：清单由声明表说了算', () => {
  it('出厂那一份：几枚都在，顺序照声明，每一枚都说了自己显示哪一页', () => {
    const faces = facesOf()

    expect(faces.map(face => face.id)).toEqual(['dossier', 'pending', 'mind-pending'])
    // 内容按 view 解析（不是一个占位，也不是按 id 写死的分支）。
    expect(faces.every(face => Boolean(face.view))).toBe(true)
  })

  it('**插件自己声明的一枚自动出现**（用户 11-25 19:5x 拍定）', () => {
    const faces = facesOf([PLUGIN_FACE])

    expect(faces.map(face => face.id)).toContain('my-face')
    const mine = faces.find(face => face.id === 'my-face')
    expect(mine?.icon).toBe('star')
    expect(mine?.title).toBe('我的那一枚')
    // 它显示哪一页也是声明说了算。
    expect(mine?.view).toBe('knowledge')
  })

  it('插件**替换**其中一枚也算数（那一枚还是那一枚，图标与名字换了）', () => {
    const faces = facesOf([
      { slotId: 'chat-card/top-right/pending', id: 'pending', icon: 'star', label: '我改的那一枚' }
    ])

    expect(faces.map(face => face.id)).toContain('pending')
    const replaced = faces.find(face => face.id === 'pending')
    expect(replaced?.icon).toBe('star')
    expect(replaced?.title).toBe('我改的那一枚')
  })

  it('填一个宿主没声明过的 slot **不算数**', () => {
    const faces = facesOf([{ slotId: 'chat-card/top-right/nope', id: 'nope', label: '填错了' }])

    expect(faces.map(face => face.id)).not.toContain('nope')
  })

  it('用户关掉的那一枚**真的不在**（不是藏起来）', () => {
    const faces = facesOf([], ['mind-pending'])

    expect(faces.map(face => face.id)).not.toContain('mind-pending')
    expect(faces.map(face => face.id)).toContain('pending')
  })

  it('档案那一枚**只在 L2**（指挥没有档案，用户 11-19 22:3x）——现在是声明里的一句话', () => {
    const dossier = facesOf().find(face => face.id === 'dossier')

    expect(dossier?.shells).toEqual(['l2'])
    // 其余两枚两个壳都有。
    expect(facesOf().find(face => face.id === 'pending')?.shells).toEqual(['l1', 'l2'])
  })
})

describe('角标那个数：属于那一格，不属于谁填它', () => {
  it('待放行那一枚的角标数＝收件箱里几件；写脑那一枚只数还批得动的', () => {
    $pendingInbox.set([
      ticket('a', '甲'),
      ticket('b', '乙')
    ])
    $mindPending.set({
      auto: false,
      count: 2,
      expired_count: 1,
      gate: true,
      items: [
        { expired: false, id: 'x', kind: 'mind_plan', paths: [], summary: '一' },
        { expired: true, id: 'y', kind: 'mind_plan', paths: [], summary: '二' }
      ]
    })

    const { getByTestId } = render(<FaceList />)
    const faces = JSON.parse(getByTestId('faces').textContent ?? '[]') as MonitorFaceMeta[]

    expect(faces.find(face => face.id === 'pending')?.count).toBe(2)
    // 过期那张点下去也不会落盘 ⇒ 不算进角标。
    expect(faces.find(face => face.id === 'mind-pending')?.count).toBe(1)
    // 档案那一枚没有角标。
    expect(faces.find(face => face.id === 'dossier')?.count).toBeUndefined()
  })
})
