/**
 * 信源活性界面侧的钉子：只钉两件事——**排序把不成形的摆前面**、**原始 id 不许上屏幕**。
 * 跑法：`npm run test:ui`（或 `vitest run --no-file-parallelism src/app/agenda/talker`）。
 * 不冻任何真实读数。
 */
import { describe, expect, it } from 'vitest'

import { buildSourceViews, type SourceLivenessPayload, type SourceLivenessRow, toStateKey } from './liveness'

const NAMES = { timetable: '课表', wechat: '微信' }

function row(patch: Partial<SourceLivenessRow>): SourceLivenessRow {
  return {
    age_days: 0.1,
    collecting: true,
    events: 240,
    last_created_at: '2026-10-16T08:15:00',
    note: '后端那句',
    source: 'timetable',
    state: 'live',
    touched_after_last_write: false,
    ...patch
  }
}

function payload(sources: SourceLivenessRow[]): SourceLivenessPayload {
  return { checked_at: '2026-10-16T10:22:00', sources, stale_after_days: 3, verdict: '一句总结' }
}

describe('toStateKey', () => {
  it('maps the four backend states and refuses to invent a fifth', () => {
    expect(toStateKey('live')).toBe('live')
    expect(toStateKey('stale')).toBe('stale')
    expect(toStateKey('never')).toBe('never')
    expect(toStateKey('no_store')).toBe('noStore')
    expect(toStateKey('green')).toBe('unknown')
  })

  it('sorts the unhealthy sources first, keeps unknown names off the screen, and reads a missing switch as unreadable', () => {
    const views = buildSourceViews(payload([
      row({ source: 'timetable' }),
      row({ source: 'zju', state: 'never', events: 0, collecting: null }),
      row({ source: 'wechat', state: 'stale', age_days: 6, collecting: false }),
      row({ source: 'dingtalk', state: 'no_store', events: 0 })
    ]), NAMES)

    expect(views.map(v => v.sourceId)).toEqual(['dingtalk', 'zju', 'wechat', 'timetable'])
    expect(views[1]?.displayName).toBeNull()
    expect(views.map(v => v.switchOff)).toEqual([false, false, true, false])
    expect(buildSourceViews(null, NAMES)).toEqual([])
  })
})
