import { atom } from 'nanostores'

import { updateAtom, type Updater } from '@/lib/store-utils'

/**
 * 当前这一屏的 **Agent 作用域**：`'l1'`，或 L2 名册行的 id（路由 `/agent/<id>`）。
 *
 * 为什么按 Agent 而不按会话：Plobi 没有「会话」这个概念——一个 Agent 就是一张嘴。
 * 终端页签要跟着这张嘴走（用户 10-31 原话「我们希望能打开不同 Agent 有不同的
 * 页签」），所以归属键必须是 Agent，而不是 `$activeSessionId`（同一个 Agent 可以
 * 有多轮对话，那些轮次之间页签该是同一套）。
 *
 * 由 `desktop-controller` 在路由层级变化时写入；`l1` 是空串的归一值，保证
 * 「裸聊」也有一个稳定的归属键。
 *
 * 单独立档而不是并进 `store/session.ts`：那份文件由在派的 `WP-AGENT-EDIT-AND-WAKE`
 * 认领（只加 `$agentCwd` 一族），同一个文件两个写者会被 Docs 门禁判相交。
 */
export const $agentScope = atom<string>('l1')

/** Set the current Agent scope. Empty / whitespace normalizes to `'l1'`. */
export const setAgentScope = (next: Updater<string>) => {
  updateAtom($agentScope, () => {
    const value = typeof next === 'function' ? next($agentScope.get()) : next

    return (value ?? '').trim() || 'l1'
  })
}
