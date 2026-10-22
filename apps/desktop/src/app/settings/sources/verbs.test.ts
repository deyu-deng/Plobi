import { describe, expect, it } from 'vitest'

import type { SourceRow } from './api'
import { groupByCategory, needsSetup, statusOf, statusReason, switchOn, visibleGroups } from './verbs'

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

describe('the three states', () => {
  it('draws exactly the three the backend is allowed to send', () => {
    expect(statusOf(row({ status: 'connected' }))).toBe('connected')
    expect(statusOf(row({ status: 'disconnected' }))).toBe('disconnected')
    expect(statusOf(row({ status: 'error' }))).toBe('error')
  })

  it('never turns an unknown word into a green dot', () => {
    // 后端给了一个这里没见过的词（旧后端、或新加的一档）⇒ 落"未连接"。
    // 画成绿点就是下一颗假绿——而这一屏最贵的账历来都是"看着像正常"。
    expect(statusOf(row({ status: undefined }))).toBe('disconnected')
    expect(statusOf(row({ status: 'collecting_possibly' as SourceRow['status'] }))).toBe('disconnected')
  })

  it('keeps the reason sentence for the error state only', () => {
    // 正常态那一行什么都不多写（用户 10-19 第二轮那条"只留最短的一句"仍然生效）。
    expect(row({ status: 'error', status_detail: '要人工验证码' }).status_detail).toBe('要人工验证码')
    expect(row({ status: 'connected', status_detail: '' }).status_detail).toBe('')

    // 后端给的是原因码（裁定 45.4：slug 不上屏），界面只查目录：① 有这一码就说那一句；
    // ② 没见过的码落兜底句，**不许空白**，也不许把码本身画出去。
    const copy = { reasonUnknown: '暂未适配', reasons: { internal: '本机采集组件暂未就绪', off: '已手动停用' } }

    expect(statusReason(row({ status: 'error', status_code: 'internal' }), copy)).toBe('本机采集组件暂未就绪')
    expect(statusReason(row({ status: 'disconnected', status_code: 'off' }), copy)).toBe('已手动停用')
    expect(statusReason(row({ status: 'disconnected', status_code: 'we_never_heard_of_this' }), copy)).toBe('暂未适配')
    expect(statusReason(row({ status: 'connected' }), copy)).toBe('暂未适配')
  })
})

describe('the switch', () => {
  it('reads null as "unreadable", not as "off"', () => {
    // 把"读不到"画成一个关着的开关，用户会以为是自己手滑关的。
    expect(switchOn(row({ enabled: null }))).toBeNull()
    expect(switchOn(row({ enabled: false }))).toBe(false)
    expect(switchOn(row({ enabled: true }))).toBe(true)
  })
})

describe('needs setup', () => {
  const ICS = { help: '', key: 'ics_path', kind: 'path' as const, label: '', required: true }
  const NOTE = { help: '', key: 'note', kind: 'text' as const, label: '', required: false }
  const SECRET = { help: '', key: 'password', kind: 'secret' as const, label: '', required: true }

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

  it('asks about a secret through "set or not", never through its value', () => {
    // 后端读数里**没有**密钥的值（那是"星号"那一格的牙齿）。所以"配没配"只能问 `secrets`。
    expect(needsSetup(row({ setup: {}, setup_fields: [SECRET] }))).toBe(true)
    expect(needsSetup(row({ setup: {}, secrets: { password: true }, setup_fields: [SECRET] }))).toBe(false)
  })
})

describe('grouping by category', () => {
  it('keeps the empty categories in the raw grouping', () => {
    const groups = groupByCategory([row({ source: 'wechat', category: 'message' })], [
      { id: 'message', sources: ['wechat'] },
      { id: 'social', sources: [] }
    ])

    expect(groups).toHaveLength(2)
    expect(groups[1]).toEqual({ category: 'social', rows: [] })
  })

  it('drops an id the read did not actually carry', () => {
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
    const groups = [
      { category: 'message', rows: [row()] },
      { category: 'social', rows: [] },
      { category: 'system', rows: [] }
    ]

    expect(visibleGroups(groups).map(g => g.category)).toEqual(['message'])
  })

  it('still shows "bring your own" because that one holds an action', () => {
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
