import { atom } from 'nanostores'

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
    vendor: string
  }
  | { agentId: string, kind: 'expert', running?: boolean, sessionId: string, title: string }

export const $openConversation = atom<null | OpenConversation>(null)

export function openConversation(next: OpenConversation) {
  $openConversation.set(next)
}

export function closeConversation() {
  $openConversation.set(null)
}
