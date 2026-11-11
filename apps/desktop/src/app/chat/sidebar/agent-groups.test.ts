import { describe, expect, it } from 'vitest'

import type { Agent } from '@/app/console/types'

import {
  agentNameSet,
  groupAgentsByCategory,
  isL1SessionProfile,
  isNavigableAgent,
  normalizeAgentCategory,
  orderedCategories
} from './agent-groups'

function agent(overrides: Partial<Agent> & { id: string; name: string }): Agent {
  return { status: 'idle', todayCalls: 0, ...overrides }
}

describe('normalizeAgentCategory', () => {
  it('passes through the four known categories (case-insensitive)', () => {
    expect(normalizeAgentCategory('projects')).toBe('projects')
    expect(normalizeAgentCategory('BUTLER')).toBe('butler')
    expect(normalizeAgentCategory(' events ')).toBe('events')
    expect(normalizeAgentCategory('Research')).toBe('research')
  })

  it('folds ONLY missing / blank into butler — legacy rows are butler-shaped', () => {
    expect(normalizeAgentCategory(undefined)).toBe('butler')
    expect(normalizeAgentCategory(null)).toBe('butler')
    expect(normalizeAgentCategory('   ')).toBe('butler')
  })

  it('keeps an unknown category verbatim (the backend owns the list)', () => {
    // 这条是现场 bug 的钉子：客户端以前持有一份四类的名单，后端新加一格品类时
    // 前端把那格折进别的栏目里 —— 名册加了 `courses`，界面上却看不见「课程」。
    expect(normalizeAgentCategory('ops')).toBe('ops')
    expect(normalizeAgentCategory(' Courses ')).toBe('courses')
  })
})

describe('groupAgentsByCategory', () => {
  it('buckets into the preferred PROJECTS/BUTLER/EVENTS/RESEARCH order', () => {
    const groups = groupAgentsByCategory([
      agent({ id: 'r1', name: '论文调研', category: 'research' }),
      agent({ id: 'p1', name: '量化交易', category: 'projects' }),
      agent({ id: 'b1', name: '日程秘书' }),
      agent({ id: 'e1', name: '日程提醒', category: 'events' })
    ])

    expect(groups.map(group => group.category)).toEqual(['projects', 'butler', 'events', 'research'])
    expect(groups[1]?.agents.map(row => row.id)).toEqual(['b1'])
    // WP-AGENT-CREATE-UPGRADE 后 courses 进偏好表第二位（projects, courses, butler,
    // events, research）——「Courses Agent 甚至不可以选中」那条的修复面。
    expect(orderedCategories(['research', 'courses', 'projects'])).toEqual(['projects', 'courses', 'research'])
  })

  it('appends a category the client has never heard of, as its own column', () => {
    const groups = groupAgentsByCategory([
      agent({ id: 'b1', name: '日程秘书' }),
      agent({ id: 'c1', name: '电工电子学', category: 'courses' }),
      agent({ id: 'c2', name: '大学物理（甲）Ⅱ', category: 'courses' })
    ])

    // courses 是偏好表里的已知品类，排在 butler 前（见 AGENT_CATEGORY_PREFERENCE）。
    expect(groups.map(group => group.category)).toEqual(['courses', 'butler'])
    expect(groups[1]?.agents.map(row => row.id)).toEqual(['b1'])
    expect(groups[0]?.agents.map(row => row.id)).toEqual(['c2', 'c1'])
    // 关键反证：课程没有混进 butler，也没有因为"不认识"被藏起来。
  })

  it('omits empty groups and sorts agents by name within a group', () => {
    const groups = groupAgentsByCategory([
      agent({ id: 'b2', name: '管家乙' }),
      agent({ id: 'b1', name: '管家甲' }),
      agent({ id: 'legacy', name: '旧代理', category: 'unknown-cat' })
    ])

    expect(groups.map(group => group.category)).toEqual(['butler', 'unknown-cat'])
    expect(groups[0]?.agents.map(row => row.id)).toEqual(['b1', 'b2'])
  })

  it('returns an empty list for no agents', () => {
    expect(groupAgentsByCategory([])).toEqual([])
  })
})

describe('agentNameSet / isL1SessionProfile', () => {
  it('collects non-blank roster names (the session ownership key)', () => {
    expect(
      agentNameSet([
        agent({ id: 'agenda', name: '日程秘书' }),
        agent({ id: '中国近现代人物研究', name: '中国近现代人物研究' }),
        agent({ id: '   ', name: 'blank' })
      ])
    ).toEqual(new Set(['agenda', '中国近现代人物研究']))
  })

  it('treats an EMPTY agent_id as L1 — and only that', () => {
    // 2026-11-05：判据只剩一条 —— 总账里 `agent_id` 空串＝无主行＝L1。
    expect(isL1SessionProfile(undefined)).toBe(true)
    expect(isL1SessionProfile(null)).toBe(true)
    expect(isL1SessionProfile('')).toBe(true)
    expect(isL1SessionProfile('   ')).toBe(true)
    // 归属非空就是某个 Agent 的会话，哪怕名册里查不到（孤儿行也不许算 L1，
    // 否则它会混进 L1 旁路归档那一刀）。
    expect(isL1SessionProfile('agenda')).toBe(false)
    expect(isL1SessionProfile('ghost')).toBe(false)
  })
})

describe('isNavigableAgent', () => {
  it('lets a real 分身 through', () => {
    expect(isNavigableAgent(agent({ id: 'nymo', name: 'Nymo' }))).toBe(true)
  })

  it('refuses a data row — because the backend says so, not because a profile is missing', () => {
    // 名册里的课程条目（role=l2_course）就是这一类。以前点它，客户端会替它编一个
    // profile 名去起后端（`agent.id` 那条回退），于是每一门都是一场重启循环。
    // 11-06 之后 `profile` 那一格已从响应里删掉，判据只剩后端的 `navigable`：
    // 课程行由后端标 false，客户端不再靠"没有 profile"这种缺字段去猜。
    expect(isNavigableAgent(agent({ id: '电工电子学', name: '电工电子学', navigable: false }))).toBe(false)
    // 没有 navigable 也不能凭"没有 profile"把它按死——缺字段是旧响应，不是判决。
    expect(isNavigableAgent(agent({ id: '电工电子学', name: '电工电子学' }))).toBe(true)
  })

  it('R-072: backend navigable=false wins', () => {
    // 课程行（heal 后 role=l2_course）与保留名行（Plobi）后端说点了必然 spawn
    // 失败 —— 行必须不可点。
    expect(isNavigableAgent(agent({ id: 'c', name: '电工电子学', navigable: false }))).toBe(false)
    expect(isNavigableAgent(agent({ id: 'p', name: 'Plobi', navigable: false }))).toBe(false)
  })

  it('R-072: backend navigable=true opens the row', () => {
    expect(isNavigableAgent(agent({ id: 'a', name: 'Aura', navigable: true }))).toBe(true)
  })

  it('R-072: navigable is independent of askable — the agenda row is both', () => {
    // agenda 走 secretary_ask（askable=false）但左栏完全点得动（navigable=true）。
    // 把 askable 当点击判据的那一版把日程秘书也变成了不可点——这就是两个字段
    // 必须分开的理由。
    expect(isNavigableAgent(agent({ id: 'agenda', name: '日程秘书', askable: false, navigable: true }))).toBe(true)
  })

  it('absent navigable means clickable — a missing field is not a verdict', () => {
    // 旧后端没透出 navigable 时按可点处理（见 agent-groups.ts 的注释）：
    // 缺字段把整栏点死，比点开报一次错更糟。
    expect(isNavigableAgent(agent({ id: 'n', name: 'Nymo' }))).toBe(true)
    expect(isNavigableAgent(agent({ id: 'z', name: 'z' }))).toBe(true)
  })
})
