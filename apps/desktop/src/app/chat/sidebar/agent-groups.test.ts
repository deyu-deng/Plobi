import { describe, expect, it } from 'vitest'

import type { Agent } from '@/app/console/types'

import {
  agentProfileIdSet,
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
    expect(orderedCategories(['research', 'courses', 'projects'])).toEqual(['projects', 'research', 'courses'])
  })

  it('appends a category the client has never heard of, as its own column', () => {
    const groups = groupAgentsByCategory([
      agent({ id: 'b1', name: '日程秘书' }),
      agent({ id: 'c1', name: '电工电子学', category: 'courses' }),
      agent({ id: 'c2', name: '大学物理（甲）Ⅱ', category: 'courses' })
    ])

    expect(groups.map(group => group.category)).toEqual(['butler', 'courses'])
    expect(groups[1]?.agents.map(row => row.id)).toEqual(['c2', 'c1'])
    // 关键反证：课程没有混进 butler，也没有因为"不认识"被藏起来。
    expect(groups[0]?.agents.map(row => row.id)).toEqual(['b1'])
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

describe('agentProfileIdSet / isL1SessionProfile', () => {
  it('collects non-blank agent profiles', () => {
    expect(
      agentProfileIdSet([
        agent({ id: 'a', name: 'A', profile: 'simulation' }),
        agent({ id: 'b', name: 'B' }),
        agent({ id: 'c', name: 'C', profile: '  ' })
      ])
    ).toEqual(new Set(['simulation']))
  })

  it('treats default/master/blank as L1 main thread (IPC sends empty profile)', () => {
    expect(isL1SessionProfile('default')).toBe(true)
    expect(isL1SessionProfile('master')).toBe(true)
    expect(isL1SessionProfile(undefined)).toBe(true)
    expect(isL1SessionProfile('')).toBe(true)
    // No agent profile set → unknown profiles conservatively stay L1.
    expect(isL1SessionProfile('simulation')).toBe(true)
  })

  it('excludes a session only when its profile is a known agent profile', () => {
    const agentProfiles = new Set(['simulation', 'l2-agenda'])

    expect(isL1SessionProfile('', agentProfiles)).toBe(true)
    expect(isL1SessionProfile('default', agentProfiles)).toBe(true)
    expect(isL1SessionProfile('master', agentProfiles)).toBe(true)
    expect(isL1SessionProfile('simulation', agentProfiles)).toBe(false)
    expect(isL1SessionProfile('l2-agenda', agentProfiles)).toBe(false)
    // Unknown profile still L1 even with a set present.
    expect(isL1SessionProfile('ghost', agentProfiles)).toBe(true)
  })
})

describe('isNavigableAgent', () => {
  it('lets a real 分身 through — it has a profile to bind', () => {
    expect(isNavigableAgent(agent({ id: 'nymo', name: 'Nymo', profile: 'nymo' }))).toBe(true)
  })

  it('refuses a data row: no profile means there is no runtime to spawn', () => {
    // 名册里的课程条目（role=l2_course）就是这一类。以前点它，客户端会替它编一个
    // profile 名去起后端（`agent.id` 那条回退），于是每一门都是一场重启循环。
    expect(isNavigableAgent(agent({ id: '电工电子学', name: '电工电子学' }))).toBe(false)
    expect(isNavigableAgent(agent({ id: 'x', name: 'x', profile: '   ' }))).toBe(false)
    expect(isNavigableAgent(agent({ id: 'y', name: 'y', profile: '' }))).toBe(false)
  })
})
