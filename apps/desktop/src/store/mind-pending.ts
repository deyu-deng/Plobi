import { atom, computed } from 'nanostores'

/**
 * 待确认（写进脑之前要点的那一下）的屏幕侧状态 —— `WP-MIND-PENDING-VISIBLE`。
 *
 * 后端是唯一真源（`plobi/mind/pending_api.py` → `/api/memory/pending`）；这里只是渲染缓存，
 * 任何动作都走服务器返回的答案往回写，不做「先乐观改本地」。
 *
 * 为什么要有这颗 store：摘要每天进闸、六小时 TTL 作废，而那一问过去只有 CLI 看得见
 * （这台现核：count 7 / expired 6）。看不见＝永远不会被点头。
 */

export interface MindPendingItem {
  id: string
  kind: string
  paths: string[]
  summary: string
  created_at?: number | string
  expired: boolean
}

export interface MindPendingState {
  items: MindPendingItem[]
  count: number
  expired_count: number
  /** 闸开没开（memory.mind_write_approval）——屏幕上要说真话，别假装能批 */
  gate: boolean
  /** 自动档（memory.mind_write_auto_approve）：开着时这一屏通常是空的，那是正常 */
  auto: boolean
  message?: string
  ok?: boolean
}

const EMPTY: MindPendingState = { items: [], count: 0, expired_count: 0, gate: true, auto: false }

export const $mindPending = atom<MindPendingState>(EMPTY)
export const $mindPendingOpen = atom(false)

/** 只数还批得动的：过期那张点下去也不会落盘，把它们算进角标＝让人去点一个死东西。 */
export const $mindPendingCount = computed($mindPending, state =>
  state.items.filter(item => !item.expired).length
)
export const $mindPendingExpiredCount = computed($mindPending, state => state.expired_count)

async function call(path: string, method?: 'POST'): Promise<MindPendingState> {
  return window.plobiDesktop.api<MindPendingState>(method ? { path, method } : { path })
}

export async function refreshMindPending(): Promise<MindPendingState> {
  try {
    const state = await call('/api/memory/pending')
    $mindPending.set({ ...EMPTY, ...state })
    return state
  } catch {
    // 读不到就当没有：这一颗角标不该因为后端一时不在线而变成谎话
    $mindPending.set(EMPTY)
    return EMPTY
  }
}

export async function actOnMindPending(id: string, verb: 'approve' | 'reject'): Promise<MindPendingState> {
  const state = await call(`/api/memory/pending/${encodeURIComponent(id)}/${verb}`, 'POST')
  $mindPending.set({ ...EMPTY, ...state })
  return state
}

export function setMindPendingOpen(open: boolean): void {
  $mindPendingOpen.set(open)
}
