import type { Agent } from '@/app/console/types'

/**
 * WP-AGENT-MODEL-PICKER-MOUNT 第二档：把「名册这一行到底跟不跟默认」这件事算清楚。
 *
 * 存在的理由是一条很容易糊过去的假话：`agent.model` 是**路由解析后的生效值**，
 * 「这行跟着默认」与「这行显式选了和默认同一个模型」在它长得一模一样。后端今天
 * （Code `97642fcb`）开始在行上带 `modelOverride` 那个布尔，但**装机副本与 10-09 那个打包壳不返这一格**
 * （现读：`desktop-build-stamp.json` builtAt 2026-10-09T01:02）——所以这里把「拿不到标记」做成
 * **第三种状态 `unknown`**，而不是让它落进 `following`。缺键不是"没覆盖"，是"没说"。
 *
 * 这颗也是第三档挂载的闸门：`canShowFollowDefault()` 假的时候，那颗「跟随默认」不许画
 * （画了要么撒谎，要么是一颗必然 400 的按钮——撤回的口子在 `WP-MODEL-OVERRIDE-CLEAR` 之前不存在）。
 */

export type RosterAgent = Agent & { modelOverride?: boolean }

export type ModelOverrideState = 'explicit' | 'following' | 'unknown'

export function overrideStateOf(agent: RosterAgent): ModelOverrideState {
  if (typeof agent.modelOverride !== 'boolean') {
    return 'unknown'
  }

  return agent.modelOverride ? 'explicit' : 'following'
}

/** 只有这一行自己说了"我显式指定"才交出那个值；following／unknown 一律 null。 */
export function explicitModelOf(agent: RosterAgent): null | string {
  return overrideStateOf(agent) === 'explicit' ? agent.model || null : null
}

/**
 * 喂给 `AgentModelPicker.overrides` 的那张表。它的契约是「这一格缺＝跟随默认」，
 * 所以**只在每一行都报得出标记时才构造**——否则 unknown 会被那张表洗成"跟随默认"。
 */
export function pickerOverrides(agents: readonly RosterAgent[]): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {}

  for (const agent of agents) {
    out[agent.id] = overrideStateOf(agent) === 'explicit' ? agent.model || '' : undefined
  }

  return out
}

/** 每一行都说得出自己跟不跟默认，才准画「跟随默认」那颗选项。空名单不算说过。 */
export function canShowFollowDefault(agents: readonly RosterAgent[]): boolean {
  return agents.length > 0 && agents.every(agent => typeof agent.modelOverride === 'boolean')
}

/** 目录里那些串是 `provider/model`；model 自己可能再带 `/`，所以只在**第一个**斜杠处切。 */
export function splitQualified(qualified: string): { model: string; provider: string } | null {
  const at = qualified.indexOf('/')

  if (at <= 0 || at === qualified.length - 1) {
    return null
  }

  const provider = qualified.slice(0, at).trim()
  const model = qualified.slice(at + 1).trim()

  if (!provider || !model) {
    return null
  }

  return { model, provider }
}
