/**
 * 进一个 L2 的那一段过渡，收成一台可测的小状态机（`WP-RAIL-SWITCH-LOADING`）。
 *
 * 为什么要单独一个文件：真实现场那条链上有三段 await（取概览 → 起/绑那个 profile 的后端 →
 * 拉会话列表与那一轮的转录），而过渡节点原先只盖住中间那一段 ⇒ 用户看到的是
 * 「空白 — 一闪 — 又空白」。把"进入中"从后端那一步里拆出来当**独立状态**，
 * 才能钉住两件事：**第一帧就得有态**，以及**任何一条出口都必须离开 entering**
 * （停在 entering 就是"拿不到当加载中"那类假象的形状）。
 *
 * 失败分三种、各说各的话（判据四那一族）：概览取不到 / 后端起不来 / 起来了但通道没绑上。
 * 三者不许汇成一句"进不去"，也不许回落到 `entering`。
 */

export type AgentEnterStatus = 'entering' | 'failed' | 'idle'

export type EnterFailureKey = 'agentEnterBindFailed' | 'agentEnterNoOverview' | 'agentEnterSpawnFailed'

export interface AgentEnterPhase {
  status: AgentEnterStatus
  /** 界面上那一句用的名字：**永远是 Agent 的显示名**，不是 profile 目录名。 */
  label: null | string
  /** 失败那一句的**文案键**（本地化在界面上做）；成功或进行中为 null。 */
  reasonKey: null | EnterFailureKey
  /** 后端原话/异常细节，跟在键那句后面；没有就空。 */
  detail: string
}

export const IDLE: AgentEnterPhase = { detail: '', label: null, reasonKey: null, status: 'idle' }

/** 点下去那一刻就有态——名字从名册缓存里拿，不等任何一次网络。 */
export function beginEntering(displayName: string): AgentEnterPhase {
  const label = (displayName || '').trim()

  return { detail: '', label: label || null, reasonKey: null, status: 'entering' }
}

export type EnterFailure = 'bind' | 'no-overview' | 'spawn'

const FAILURES: Record<EnterFailure, EnterFailureKey> = {
  'no-overview': 'agentEnterNoOverview',
  spawn: 'agentEnterSpawnFailed',
  bind: 'agentEnterBindFailed',
}

/** 三种失败是三个**键**，不是同一句话换三个前缀；未知种类不许挑一个最像的顶上去。 */
export function enterFailure(code: EnterFailure, detail = ''): AgentEnterPhase {
  const key = FAILURES[code] ?? null

  return { detail: key ? detail : `没记到的进入失败种类 ${String(code)}：不猜它属于哪一种。`,
           label: null, reasonKey: key, status: 'failed' }
}

/** 成功与"这一趟作废了"（切到别的分身）都走这里：不许把 entering 留在场上。 */
export function endEntering(): AgentEnterPhase {
  return IDLE
}

/**
 * 该不该给中间栏一个过渡节点。
 *
 * `failed` **不再给过渡节点**（那时给的就是"看起来像加载中"），它给的是那一句人话；
 * `idle` 也不给——否则一次正常的进入结束后会留一张永远转不完的圈。
 */
export function showsTransition(phase: AgentEnterPhase): boolean {
  return phase.status === 'entering'
}

export interface EnterAgentRow {
  id?: string
  name?: string
  profile?: string
}

/**
 * 后端切换那一句里允许出现的那个名字。
 *
 * 名册里查得到就用它的名字；查不到回 `null` —— 调用方因此**必须**走中性那一句，
 * 而不是把 `l2-agenda` 这类目录名打在屏幕上（左栏早就按 WP-UI-NO-INTERNALS 改成简称，
 * 遮罩这一格当时漏了，`WP-RAIL-AGENT-EQUAL` 一起收掉）。
 */
export function swapLabel(agents: readonly EnterAgentRow[], profile: null | string): null | string {
  const key = (profile || '').trim().toLowerCase()

  if (!key) {
    return null
  }

  const hit = agents.find(agent => (agent.profile || '').trim().toLowerCase() === key)

  return (hit?.name || '').trim() || null
}
