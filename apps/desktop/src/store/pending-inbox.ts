import { atom, computed } from 'nanostores'

import type { DispatchTicket } from '@/app/chat/sidebar/ticket-badge'

/**
 * 「待放行」那一面的**当前快照**。
 *
 * 为什么住在 store 而不是组件里：这一族三枚入口共用**同一个组件**
 * （`monitor-entry-button.tsx`），而角标上那个数要在**面板关着的时候**也一直准 ⇒
 * 轮询与快照都不能跟着面板的生死走。
 *
 * **取数与动作一个字没动**（还是 `pending-tickets.tsx` 里那几条路）：
 * 这一档只放「快照放哪儿」这一件事。
 */

/** 收件箱里的一件：票（派活/汇报）或授权 lesson（草稿/已生效）。 */
export type PendingInboxItem =
  | (DispatchTicket & {
    kind: 'ticket'
    /** 旧口径的整句（后端拼好的一句话）：屏上只用它兜底，见裁定 236。 */
    one_line?: string
    /** 需人六态的码（`ready` / `blocked` …）；外部事件票没有这一格。 */
    state?: string
    /** 回报的那一位（`ticket_reports.reporter`）。 */
    who?: string
    /** 回执时刻（毫秒）；0／缺省表示没有时间可印。 */
    at_ms?: number
    /** 回执正文（它真正说了什么）——主行优先用它。 */
    note?: string
  })
  | {
    kind: 'lesson'
    id: string
    status: string
    scope: string
    rule: string
    question: string
    words: string
  }

/** 收件箱里那几件（最新一次拉取的结果）。 */
export const $pendingInbox = atom<PendingInboxItem[]>([])

/** 清单读不出来（≠ 一件都没有）：屏上要说读不到，不许糊成空表。 */
export const $pendingInboxFailed = atom(false)

/** 动作失败了（接纳／拒绝／评论那一路回来的话）。 */
export const $pendingInboxError = atom('')

/** 角标上那个数＝清单里有几件。 */
export const $pendingInboxCount = computed($pendingInbox, items => items.length)
