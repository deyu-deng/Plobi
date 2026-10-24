/**
 * 信源活性界面侧的钉子：只钉两件事——**排序把不成形的摆前面**、**原始 id 不许上屏幕**。
 * 跑法：`npm run test:ui`（或 `vitest run --no-file-parallelism src/app/agenda/talker`）。
 * 不冻任何真实读数。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildSourceViews, type SourceLivenessPayload, type SourceLivenessRow, toStateKey } from './liveness'

const NAMES = { timetable: '课表', wechat: '微信' }

function row(patch: Partial<SourceLivenessRow> = {}): SourceLivenessRow {
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

  it('takes no source row when the payload has no array to walk', () => {
    // 10-23 真崩过一次：那座桥回的是整个信封 `{ok, data}`，取数那一侧没剥就把 `undefined`
    // 交给 `.map`，整棵组件树进 error boundary（面板不见，用户对着一个空白猜）。
    // 所以：`null`、有对象但没数组、以及"信封没剥"这一种，都只能回空列表，不许抛。
    const broken = { checked_at: '', sources: undefined } as unknown as SourceLivenessPayload

    expect(buildSourceViews(null, {})).toEqual([])
    expect(buildSourceViews(broken, {})).toEqual([])
    expect(buildSourceViews({ ok: true, data: broken } as unknown as SourceLivenessPayload, {})).toEqual([])
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

describe('getSourceLiveness', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    delete (window as { plobiDesktop?: unknown }).plobiDesktop
  })

  it('peels the envelope before handing the rows to the panel', async () => {
    // 桥那一侧不剥信封（`settings/sources/api.ts` 一直在剥，这一只没剥 ⇒ 10-23 崩在屏上）。
    const calls: string[] = []

    ;(window as unknown as { plobiDesktop: unknown }).plobiDesktop = {
      api: async ({ path }: { path: string }) => {
        calls.push(path)

        return { ok: true, data: { checked_at: '2026-10-23T16:00:00+08:00', sources: [row()] } }
      }
    }

    const { getSourceLiveness } = await import('./api')
    const payload = await getSourceLiveness()

    expect(calls).toEqual(['/api/collect/sources'])
    expect(payload?.sources).toHaveLength(1)
  })

  it('reads an envelope without rows as "nothing to show" rather than throwing', async () => {
    ;(window as unknown as { plobiDesktop: unknown }).plobiDesktop = {
      api: async () => ({ ok: true, data: {} })
    }

    const { getSourceLiveness } = await import('./api')

    expect(await getSourceLiveness()).toBeNull()
  })
})
