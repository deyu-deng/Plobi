import { atom } from 'nanostores'

/**
 * 「把这段话交给另一个智能体」的那笔待办（WP-SELECTION-TOOLBAR）。
 *
 * 为什么要有这一档：选区浮窗里点「发给别的智能体」时，**当前这一屏并不是那个智能体**
 * ——屏幕要跳过去、后端也要换成它那一份，发送得等换完才能发生。所以这段话先
 * 存一枚待办，等目标那一屏的输入框长出来、作用域对上了，再照平常那条路发出去。
 *
 * **取出即清**：同一条话不许被两个输入框各发一次。
 */

export interface AgentHandoff {
  /** 目标智能体在名册里的身份键（＝它的 `id`）。 */
  agentId: string
  /** 目标那一屏的作用域键（`agent:<id>`）——取待办时按它对账。 */
  targetKey: string
  text: string
}

export const $agentHandoff = atom<AgentHandoff | null>(null)

/** 记下一笔待办。 */
export function stageAgentHandoff(handoff: AgentHandoff): void {
  $agentHandoff.set(handoff)
}

/**
 * 取出那笔待办——**只有作用域键对得上才取**（不会把话发到当前这个智能体嘴里），
 * 取走即清。
 */
export function takeAgentHandoff(targetKey: string): AgentHandoff | null {
  const current = $agentHandoff.get()

  if (!current || current.targetKey !== targetKey) {
    return null
  }

  $agentHandoff.set(null)

  return current
}

/** 丢掉待办（目标那一屏进不去、或用户改主意了）。 */
export function clearAgentHandoff(): void {
  $agentHandoff.set(null)
}
