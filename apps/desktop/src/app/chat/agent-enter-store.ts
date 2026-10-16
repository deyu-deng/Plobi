/**
 * `$agentEnter`：进一个 L2 那一段过渡的**在场记号**（`WP-RAIL-SWITCH-LOADING`）。
 *
 * 纯状态机在同目录的 `agent-enter.ts`，这一档只把它挂到 nanostores 上——
 * 分开是为了能测：那条链上有三段 await 与六条出口，把"该不该有过渡节点"写成
 * 可断言的函数，比写在 JSX 里稳。
 */

import { atom } from 'nanostores'

import { type AgentEnterPhase, endEntering } from './agent-enter'

export const $agentEnter = atom<AgentEnterPhase>(endEntering())

/** 只有还挂在"进入中"时才清掉：失败那句要留在场上，否则人话一闪就没。 */
export function settleAgentEnter(): void {
  if ($agentEnter.get().status === 'entering') {
    $agentEnter.set(endEntering())
  }
}
