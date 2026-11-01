/**
 * The edits behind the bottom-bar switch, in one file both panes use.
 *
 * 用户 11-01：控制从设置页搬到底栏那个 overlay。读数不在这儿——`GET /api/network/status`
 * 的 `switch` 那一格是后端给的（`agent/egress/switch.py`，裁定 94：面板不许再从 rules 自己
 * 推一遍「现在是谁在管」）。这里只有**写法**：把一次点击变成整份 `network` 段里最小的一次改动，
 * 因为保存面是整段替换（PUT /api/network/egress），少带一个键就等于删掉一个键。
 *
 * 三条判据都落在测试里，不落在注释里：
 *  · 换承载是**互斥的三选一**，不是能叠加的两个开关；
 *  · 「全局」只摘那条分类直连，兜底规则照旧在最后；
 *  · 选节点只动兜底规则，别的规则一条不碰。
 */

import type { NetworkEgressBlock, NetworkEgressEngine, NetworkEgressRule } from '@/types/plobi'

/** The classification rule that *is* "按规则"; the backend names the same string. */
export const RULE_MODE_MATCH = 'geosite:cn'

export type Transport = 'off' | 'system' | 'tun'
export type RuleMode = 'global' | 'rule'

/** `engine.tun` is in the table but not yet in the shared type (that file has a live owner). */
export interface EgressEngine extends NetworkEgressEngine {
  tun?: Record<string, unknown>
}

export interface EgressBlock extends NetworkEgressBlock {
  engine?: EgressEngine
}

/** What `status.switch` answers — the reading the controls bind to. */
export interface EgressSwitch {
  exit: string
  mode: RuleMode
  transport: Transport
  vpn: boolean
}

/** What `status.tun` answers: the guard's reading plus the block to write. */
export interface EgressTun {
  admin?: boolean
  block?: Record<string, unknown>
  holders?: { description?: string; metric?: number; name?: string }[]
  ok?: boolean
  reasons?: string[]
  requested?: boolean
  wintun?: { found?: boolean; target?: string; where?: string }
}

export interface EgressStatusExtra {
  membership?: { groups?: Record<string, string[]>; nodes?: string[] }
  switch?: EgressSwitch
  tun?: EgressTun
}

export function asEgressBlock(block: NetworkEgressBlock | undefined | null): EgressBlock {
  return (block ?? {}) as EgressBlock
}

function copy(block: EgressBlock): EgressBlock {
  return { ...block, rules: (block.rules ?? []).map(rule => ({ ...rule })) }
}

function catchAllIndex(rules: NetworkEgressRule[]): number {
  return rules.length && rules[rules.length - 1].match.trim() === '*' ? rules.length - 1 : -1
}

/**
 * Ask for one machine-wide carrier. The other one is turned off in the same breath —
 * TUN and the system proxy are **modes** here, not two switches that may both sit on,
 * because a machine with both has two answers to "where did this go" and the panel can
 * only print one of them.
 */
export function patchTransport(block: EgressBlock, transport: Transport, tunBlock: Record<string, unknown> = {}): EgressBlock {
  const out = copy(block)
  const engine = { ...(out.engine ?? {}) }
  const carried = { ...((engine.tun ?? {}) as Record<string, unknown>) }

  engine.tun = transport === 'tun' ? { ...carried, ...tunBlock, enabled: true } : { ...carried, enabled: false }
  out.engine = engine
  out.system_proxy = { enabled: transport === 'system' }
  return out
}

/** 全局 = 把 `geosite:cn → direct` 摘掉；按规则 = 把它插回**兜底之前**。 */
export function patchMode(block: EgressBlock, mode: RuleMode): EgressBlock {
  const out = copy(block)
  const kept = (out.rules ?? []).filter(rule => rule.match.trim() !== RULE_MODE_MATCH)

  if (mode === 'rule') {
    const last = catchAllIndex(kept)
    const entry: NetworkEgressRule = { match: RULE_MODE_MATCH, via: 'direct' }
    out.rules = last >= 0 ? [...kept.slice(0, last), entry, kept[last]] : [...kept, entry]
  } else {
    out.rules = kept
  }
  return out
}

/** 选节点：兜底规则指过去。表里还没有兜底规则就补一条——「默认出口」必须意味着"没匹配上的都去哪"。 */
export function patchExit(block: EgressBlock, name: string): EgressBlock {
  const target = name.trim()
  if (!target) throw new Error('an exit name is required')
  const out = copy(block)
  const rules = [...(out.rules ?? [])]
  const via = `proxy:${target}`
  const last = catchAllIndex(rules)

  if (last >= 0) rules[last] = { ...rules[last], via }
  else rules.push({ match: '*', via })
  out.rules = rules
  return out
}

/** 老设置页那两条「按规则 / 全部走代理」的按钮语义，现在只有这一份实现。 */
export function exitOf(block: EgressBlock): string {
  const rules = block.rules ?? []
  const via = rules.length ? rules[rules.length - 1].via : ''
  return via.startsWith('proxy:') ? via.slice('proxy:'.length) : ''
}

export function modeOf(block: EgressBlock): RuleMode {
  return (block.rules ?? []).some(rule => rule.match.trim() === RULE_MODE_MATCH && rule.via === 'direct')
    ? 'rule'
    : 'global'
}

/** Only the fallback path uses this — the reading normally comes from `status.switch`. */
export function transportOf(block: EgressBlock): Transport {
  if (block.engine?.tun?.enabled === true) return 'tun'
  if (block.system_proxy?.enabled) return 'system'
  return 'off'
}

export interface SystemProxyReading {
  declared?: boolean
  ours?: boolean
  supported?: boolean
}

/**
 * 「表说要接管」和「注册表现在是我们的」是两件事，四句话不许合并成一句（这台 10-31 实测：
 * 别的程序在我们接管之后自己把注册表改回去了，屏幕上那个开关当时还亮着）。
 *
 * 它是 tooltip 的句子，不是屏幕上的正文——状态用形状说（用户 10-27），话放在悬停里。
 * 少一个读数就说没有：没有 `ours` 不等于 `false`，那会把"读不到"报成"没接管"。
 */
export function proxyTooltip(proxy?: SystemProxyReading): string {
  if (!proxy) return ''
  if (!proxy.supported) return '这一格目前只实现 Windows'
  if (proxy.ours) return 'Plobi 在管这台机器的系统代理'
  return proxy.declared ? '表里说要接管，但注册表里不是我们的' : '不是 Plobi 的（跟着系统或别的程序）'
}
