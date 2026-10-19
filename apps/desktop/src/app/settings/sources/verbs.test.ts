import { describe, expect, it } from 'vitest'

import type { SourceRow } from './api'
import { groupByCategory, needsSetup, pillFor, pillTone, ruleFor, VERB_RULES, verbFor, visibleGroups } from './verbs'

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
    expect(pillFor(row({ enabled: null }))).toBe('pillUnknown')
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

  it('gives no verb to a hand-written row that is not a collector source', () => {
    expect(verbFor(row({ enabled: null, category: null, state: 'stale' }))).toBeNull()
  })
})

describe('the pill', () => {
  it('comes from the SAME rule as the verb, never a second table', () => {
    // 分开算就会出现「胶囊说开着、按钮说试一次」那种自相矛盾的一行。
    const cases = [
      { enabled: true, has_builder: false, state: 'never' },
      { enabled: false, state: 'never' },
      { enabled: true, has_builder: true, state: 'never' },
      { enabled: true, has_builder: true, state: 'stale' },
      { enabled: true, has_builder: true, state: 'no_store' },
      { enabled: true, has_builder: true, state: 'live' },
      { enabled: null, state: 'x' }
    ]

    for (const one of cases) {
      const r = row(one)

      const expected: Record<string, string> = {
        live: 'pillLive',
        noBuilder: 'noBuilder',
        noStore: 'pillNoStore',
        switchOff: 'pillSwitchOff',
        switchOnNotLive: one.state === 'never' ? 'pillNotStarted' : 'pillStale',
        unknown: 'pillUnknown'
      }

      expect([r, pillFor(r)]).toBeTruthy()
      expect(pillFor(r)).toBe(expected[ruleFor(r)])
    }
  })

  it('marks only "collecting" as the good one', () => {
    // 不给别的颜色等于在说"有话要说"；只有一个 primary 才不会互相盖过。
    expect(pillTone(row({ enabled: true, has_builder: true, state: 'live' }))).toBe('primary')
    expect(pillTone(row({ enabled: true, has_builder: true, state: 'stale' }))).toBe('muted')
    expect(pillTone(row({ enabled: false }))).toBe('muted')
    expect(pillTone(row({ enabled: null }))).toBe('muted')
  })

  it('never shows the collector note on the row', () => {
    // 用户 10-19 第二轮：只留胶囊。那段 note 是给工程师看的（还带 `**` 星号），
    // 渲染出来等于把内部文档贴到用户脸上。
    const r = row({ enabled: true, has_builder: true, note: '阈值内没有新条目进来。它**不等于**…', state: 'live' })

    expect(pillFor(r)).toBe('pillLive')
    expect(pillTone(r)).toBe('primary')
  })
})

describe('needs setup', () => {
  const ICS = { help: '', key: 'ics_path', kind: 'path' as const, label: '', required: true }
  const NOTE = { help: '', key: 'note', kind: 'text' as const, label: '', required: false }

  it('is false for a source that declares no fields at all', () => {
    expect(needsSetup(row({ setup_fields: [] }))).toBe(false)
  })

  it('is true while a required field is still empty', () => {
    expect(needsSetup(row({ setup: {}, setup_fields: [ICS] }))).toBe(true)
  })

  it('is false once every required field has a value', () => {
    expect(needsSetup(row({ setup: { ics_path: 'D:/x.ics' }, setup_fields: [ICS] }))).toBe(false)
  })

  it('does not care about the optional fields', () => {
    expect(needsSetup(row({ setup: { ics_path: 'D:/x.ics' }, setup_fields: [ICS, NOTE] }))).toBe(false)
  })

  it('treats a whitespace-only value as still empty', () => {
    expect(needsSetup(row({ setup: { ics_path: '   ' }, setup_fields: [ICS] }))).toBe(true)
  })
})

describe('grouping by category', () => {
  it('keeps the empty categories in the raw grouping', () => {
    // 分组本身不该丢信息 —— 丢的是**渲染**（见下面那条）。
    const groups = groupByCategory([row({ source: 'wechat', category: 'message' })], [
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

describe('what actually gets rendered', () => {
  it('hides the empty categories entirely', () => {
    // 用户 10-19：「很多东西都是没必要展示给用户的信息」。三个「这一类还没接任何信源」
    // 占掉半屏，而那不是信息，是分类表自己的形状。
    const groups = [
      { category: 'message', rows: [row()] },
      { category: 'social', rows: [] },
      { category: 'system', rows: [] }
    ]

    expect(visibleGroups(groups).map(g => g.category)).toEqual(['message'])
  })

  it('still shows "bring your own" because that one holds an action', () => {
    // 「接一个自己的」不当分类，它**就是**那个逃生口——空着的时候也得在，
    // 否则那个唯一的装包入口就跟着空分类一起消失了。
    const groups = [
      { category: 'message', rows: [row()] },
      { category: 'custom', rows: [] }
    ]

    expect(visibleGroups(groups).map(g => g.category)).toEqual(['message', 'custom'])
  })

  it('does not special-case any other empty category', () => {
    const groups = ['local', 'social', 'system', 'website'].map(category => ({ category, rows: [] }))

    expect(visibleGroups(groups)).toEqual([])
  })
})