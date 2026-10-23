/**
 * Talker collection status — the data side of A7 (see
 * `Docs/ROADMAP.md` A7).
 *
 * Backed by the collector's `plobi/collectors/chatlog/state.py` status table
 * through `/api/collect/*` (mounted in `plobi_cli/web_server.py`). The board
 * entry consumes this module unchanged; the backend owns the truth.
 *
 * Privacy contract (ADR-0010, fail-closed): a brand-new talker is NEVER
 * collected by default — it surfaces as `pending` for a one-click
 * collect/exclude. `reviewComplete` stays false on first enable, during which
 * nothing is collected.
 */

import type { SourceLivenessPayload } from './liveness'

export type TalkerMode = 'collect' | 'exclude'
export type TalkerStatus = 'known' | 'excluded' | 'pending'

export interface TalkerState {
  id: string
  name: string
  /** Last activity date (naive local ISO date), for sorting/review. */
  lastActive?: string
  status: TalkerStatus
}

export interface TalkerCollection {
  /** False on first enable — nothing is collected until the review is done. */
  reviewComplete: boolean
  talkers: TalkerState[]
}

export async function getTalkerCollection(): Promise<TalkerCollection> {
  return window.plobiDesktop.api<TalkerCollection>({ path: '/api/collect/talkers' })
}

export async function setTalkerMode(id: string, mode: TalkerMode): Promise<void> {
  await window.plobiDesktop.api<{ id: string; status: TalkerStatus }>({
    path: `/api/collect/talkers/${encodeURIComponent(id)}/mode`,
    method: 'POST',
    body: { mode }
  })
}

/**
 * Batch decision over the EXISTING `/api/collect/talkers/bulk` endpoint (no new
 * REST surface) — used by "exclude all pending official accounts". `pending`
 * is not a legal decision (fail-closed default), hence the narrowed status.
 */
export async function bulkSetTalkerMode(
  ids: string[],
  status: Extract<TalkerStatus, 'excluded' | 'known'>
): Promise<void> {
  await window.plobiDesktop.api<{ status: TalkerStatus; updated: number }>({
    path: '/api/collect/talkers/bulk',
    method: 'POST',
    body: { status, talkers: ids }
  })
}

export async function completeReview(): Promise<void> {
  await window.plobiDesktop.api<{ reviewComplete: boolean }>({
    path: '/api/collect/review-complete',
    method: 'POST'
  })
}


/**
 * 信源活性读数（`WP-COLLECT-WIN` 的界面那半边）。
 *
 * 放在这里而不是新模块：本档是这个面板**唯一**的取数入口，测试整体 mock 它，
 * 组件因此能在没有 `window.plobiDesktop` 的环境里安静地什么都不画。
 */
export async function getSourceLiveness(): Promise<SourceLivenessPayload | null> {
  const envelope = await window.plobiDesktop.api<{ data?: SourceLivenessPayload } | SourceLivenessPayload>(
    { path: '/api/collect/sources' }
  )

  // 这座桥回的是**整个信封** `{ok, data}`（`settings/sources/api.ts` 那边一直在剥它，
  // 这一只忘了）。不剥就是 `payload.sources` 为 undefined，`.map` 当场把整棵树送进
  // error boundary——10-23 挂在信源抽屉里第一次真渲染就炸给用户看了。
  const payload = (envelope as { data?: SourceLivenessPayload } | null)?.data ?? envelope

  return payload && Array.isArray((payload as SourceLivenessPayload).sources)
    ? (payload as SourceLivenessPayload)
    : null
}
