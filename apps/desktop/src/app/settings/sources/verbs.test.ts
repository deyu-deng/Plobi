import { describe, expect, it } from 'vitest'

import type { SourceRow } from './api'
import { groupByCategory, ruleFor, VERB_RULES, verbFor } from './verbs'

function row(over: Partial<SourceRow> = {}): SourceRow {
  return {
    age_days: null,
    category: 'message',
    collecting: null,
    enabled: null,
    enabled_origin: null,
    events: 0,
    has_builder: null,
    last_created_at: '',
    note: '',
    requires_manual_setup: null,
    setup: null,
    setup_fields: [],
    source: 'x',
    state: 'unknown',
    touched_after_last_write: false,
    ...over
  }
}

describe('the verb table', () => {
  it('gives every state one row of the shipped table', () => {
    // §6.1 那张表的每一格都在这里，且次序没变。下标就是规格里的次序。
    expect(VERB_RULES.map(r => r.id)).toEqual([
      'noBuilder',
      'switchOff',
      'switchOnNotLive',
      'noStore',
      'live',
      'unknown'
    ])
  })

  it('refuses to invent a verb for a product defect', () => {
    // A：注册表说在采，却没有任何一方构造得出执行方 —— 给了「打开」就是骗人。
    expect(verbFor(row({ enabled: true, has_builder: false, state: 'never' }))).toBeNull()
    expect(ruleFor(row({ enabled: true, has_builder: false }))).toBe('noBuilder')
  })

  it('offers "enable" only when the switch is genuinely off', () => {
    expect(verbFor(row({ enabled: false, state: 'never' }))).toBe('enable')
  })

  it('never renders an unreadable switch as one that is off', () => {
    // 读不到开关（null）≠ 没点。把 null 画成"关着的开关"就是一颗假绿。
    expect(verbFor(row({ enabled: null, state: 'live' }))).toBeNull()
    expect(verbFor(row({ enabled: null, state: 'never' }))).toBeNull()
    expect(ruleFor(row({ enabled: null }))).toBe('unknown')
  })

  it.each(['never', 'stale'] as const)('offers "try" for %s when the switch is on', state => {
    expect(verbFor(row({ enabled: true, has_builder: true, state }))).toBe('try')
    expect(ruleFor(row({ enabled: true, state }))).toBe('switchOnNotLive')
  })

  it('offers "disable" only for a source that is actually alive', () => {
    expect(verbFor(row({ enabled: true, has_builder: true, state: 'live' }))).toBe('disable')
  })

  it('offers nothing when the store cannot be read', () => {
    expect(verbFor(row({ enabled: true, has_builder: true, state: 'no_store' }))).toBeNull()
  })

  it('lets an unreadable store outrank the alive rule', () => {
    // no_store 在 live 之前：读不到库的时候，"还在进"也是猜的。
    expect(ruleFor(row({ enabled: true, has_builder: true, state: 'no_store' }))).toBe('noStore')
  })

  it('gives no verb to a hand-written row that is not a collector source', () => {
    expect(verbFor(row({ enabled: null, category: null, state: 'stale' }))).toBeNull()
  })
})

describe('grouping by category', () => {
  it('keeps the empty categories', () => {
    const rows = [row({ source: 'wechat', category: 'message' })]

    const groups = groupByCategory(rows, [
      { id: 'message', sources: ['wechat'] },
      { id: 'social', sources: [] }
    ])

    expect(groups).toHaveLength(2)
    expect(groups[1]).toEqual({ category: 'social', rows: [] })
  })

  it('drops an id the read did not actually carry', () => {
    // 后端给了名单但读数里没这一行 —— 不许拿一个空的壳去渲染，那会画出一条
    // "什么都不知道"的行，而"没读到"和"真的什么都没有"是两件事。
    const groups = groupByCategory([], [{ id: 'message', sources: ['wechat', 'ghost'] }])

    expect(groups[0].rows).toEqual([])
  })

  it('follows the category order the backend sent, not the alphabet', () => {
    const groups = groupByCategory([], [
      { id: 'website', sources: [] },
      { id: 'message', sources: [] },
      { id: 'local', sources: [] }
    ])

    expect(groups.map(g => g.category)).toEqual(['website', 'message', 'local'])
  })
})
