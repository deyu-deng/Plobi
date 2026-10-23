/**
 * 信源活性（`GET /api/collect/sources`）的界面侧。
 *
 * 这个探针早就有了，但**全仓没有一个消费方**——所以"某条信源没在采"这件事在界面上是静音的：
 * 用户看到的是一条空列表，而不是"微信这条从没进过库"。本模块把那句话拼出来：
 * **状态与时刻是读数，句子是本地化的**，二者都不许把原始 id 打在屏幕上。
 */

export type SourceLivenessState = 'live' | 'never' | 'no_store' | 'stale'

export const STATE_KEYS = ['live', 'noStore', 'never', 'stale', 'unknown'] as const
export type LivenessStateKey = typeof STATE_KEYS[number]

const STATE_MAP: Record<string, LivenessStateKey> = {
  live: 'live',
  never: 'never',
  no_store: 'noStore',
  stale: 'stale'
}

/** 越不成形的排前面：读不到库 > 从来没采过 > 停了 > 还在进。 */
const SEVERITY: Record<LivenessStateKey, number> = {
  live: 4,
  noStore: 0,
  never: 1,
  stale: 2,
  unknown: 3
}

export interface SourceLivenessRow {
  age_days: null | number
  collecting: null | boolean
  events: number
  last_created_at: string
  note: string
  source: string
  state: SourceLivenessState | (string & {})
  touched_after_last_write: boolean
}

export interface SourceLivenessPayload {
  checked_at: string
  sources: SourceLivenessRow[]
  stale_after_days: number
  verdict: string
}

export interface SourceView {
  /** 名册里查得到的显示名；查不到就是 null ⇒ 渲染侧走那句"没记到显示名"，不许打印原始 id。 */
  displayName: null | string
  /** 开关没点。`collecting === null`（这一条不在注册表里，如 `manual`）**不算没点**。 */
  switchOff: boolean
  ageDays: null | number
  events: number
  sourceId: string
  stateKey: LivenessStateKey
  touched: boolean
}

export function toStateKey(state: string): LivenessStateKey {
  return STATE_MAP[state] ?? 'unknown'
}

export function buildSourceViews(
  payload: null | SourceLivenessPayload,
  names: Record<string, string>
): SourceView[] {
  // 两道都挡：`null`（取数失败）与"有对象但没有那一格数组"（后端比面旧、或信封没剥）。
  // 这一格崩过一次是把整棵组件树送进 error boundary，用户看到的是整个面板不见。
  if (!payload || !Array.isArray(payload.sources)) {
    return []
  }

  return payload.sources
    .map(row => ({
      ageDays: row.age_days ?? null,
      displayName: names[row.source] ?? null,
      events: row.events,
      sourceId: row.source,
      stateKey: toStateKey(row.state),
      switchOff: row.collecting === false,
      touched: row.touched_after_last_write
    }))
    .sort((a, b) => SEVERITY[a.stateKey] - SEVERITY[b.stateKey]
      || (a.displayName ?? a.sourceId).localeCompare(b.displayName ?? b.sourceId))
}
