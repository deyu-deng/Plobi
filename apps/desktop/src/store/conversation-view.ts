import { atom } from 'nanostores'

import type { ExpertRoleKey } from '@/shared/expert-trades'

/**
 * 主区正在看的那条「外面的对话」（用户 11-01：不要弹窗，直接复用对话卡片）。
 *
 * 两种都是「不在这个 Agent 自己的会话里」的对话：
 * - `external` —— 你在别家 AI 软件里跟这个 Agent 有关的会话（能发就还能在那家软件里继续说话）；
 * - `expert`   —— L2 派出去的那个临时专家自己的工作日志（只读，它已经干完了）。
 *
 * 只放**最小可还原**的四个字段：主区拿它去取数，不需要再问左栏。
 * `agentId` 也带着：换 Agent 时主区能自己判断这条还属不属于当前这摊。
 */
export type OpenConversation =
  | {
    agentId: string
    kind: 'external'
    /** 那家软件支不支持从这里说话（`ExternalSession.sendable`）。 */
    sendable: boolean
    sessionId: string
    title: string
    /**
     * 这条会话最后一句话的时间（`ExternalSession.updated_at`，本地时间串）。
     * **抬头要它**：左栏那一行有时间，点开就没了 ⇒ 两个屏对不上。
     * 后端早就在返回里给全了，这里只是把它带过去（协议不动）。
     */
    updatedAt?: string
    vendor: string
  }
  | {
    agentId: string
    kind: 'expert'
    /**
     * 哪一类角色（file/browser/software/computer）。
     * 名册上四行**永远点得开**：没派过活时也要能打开这张卡，看到它是什么角色、能干什么，
     * 而不是像以前那样把整行 `disabled` 掉、点了毫无反应（11-04 用户：「根本点击不了」）。
     */
    role?: ExpertRoleKey
    running?: boolean
    /** 没派过活就没有会话号——主区显示角色说明，而不是去读一个不存在的日志。 */
    sessionId: null | string
    title: string
  }

/**
 * 名手分类键的类型从 `shared/expert-trades.ts` 那里来（2026-11-15 收口）。
 *
 * 以前这里手写一份字面量联合、`expert-roles.ts` 再写一份、后端 `ROLES` 写第三份：
 * 三处不同步就得到一行点不开的空行。现在键只在 `shared/` 定义一次，store 与 UI 都引用它，
 * store 依然不反向依赖 UI 模块。
 */
export type { ExpertRoleKey } from '@/shared/expert-trades'

export const $openConversation = atom<null | OpenConversation>(null)

export function openConversation(next: OpenConversation) {
  $openConversation.set(next)
}

export function closeConversation() {
  $openConversation.set(null)
}
