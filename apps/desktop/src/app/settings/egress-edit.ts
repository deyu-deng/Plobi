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

  if (!target) {throw new Error('an exit name is required')}
  const out = copy(block)
  const rules = [...(out.rules ?? [])]
  const via = `proxy:${target}`
  const last = catchAllIndex(rules)

  if (last >= 0) {rules[last] = { ...rules[last], via }}
  else {rules.push({ match: '*', via })}

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
  if (block.engine?.tun?.enabled === true) {return 'tun'}

  if (block.system_proxy?.enabled) {return 'system'}

  return 'off'
}

/**
 * 规则那一屏的读写：识别、增、删、排序、改去向。
 *
 * 用户 11-22「加上完整的规则细化体验」：模式只有「规则/全局」两挡时，规则屏其实只有一枚
 * `geosite:cn → direct`，于是"按规则"这四个字背后能细分到什么程度，屏上完全看不出来 ——
 * 想给 `geoip:cn` 直连、想让某个进程绕过、想把一段局域网网段排除，都得手改 config.yaml。
 * 这一屏把表本身摆出来：一行一条，语法照后端 `agent/egress/rules.py` 的同一套前缀认，
 * 认识的写在屏幕上、不认识的当场说是哪一段不对，而不是等保存时后端回一句 400。
 *
 * 这里只有**写法**（同本文件开头那三条判据）：哪一条排在哪、谁不许动，都由后端那份解析器
 * 说了算；本模块只在它前面挡掉明显写不出来的东西。
 */
export type RuleKind =
  | 'catchAll'
  | 'cidr'
  | 'domain'
  | 'geoip'
  | 'geosite'
  | 'keyword'
  | 'process'
  | 'regex'
  | 'src'
  | 'suffix'

export type RuleProblem = 'badPrefix' | 'empty' | 'emptyPayload' | 'space' | 'wildcard'

export interface RuleReading {
  error?: RuleProblem
  kind: RuleKind
  payload: string
}

/** 前缀 → kind。与 `agent/egress/rules.py` 的 `_KIND_PREFIX` 是同一张表。 */
const PREFIXED: Record<string, RuleKind> = {
  geosite: 'geosite',
  geoip: 'geoip',
  keyword: 'keyword',
  process: 'process',
  regex: 'regex',
  src: 'src'
}

/**
 * 把一行匹配读成「它是什么」。判据全在这里，屏幕只负责把它翻成一句话。
 *
 * 顺序有讲究：`regex:` 与 `process:` 的值里有冒号/空格/反斜杠都是正常的（`regex:^ad\.`、
 * `process:C:\a b.exe`），所以先认前缀再看内容，别拿"像不像域名"去猜。
 */
export function readRule(match: string): RuleReading {
  const raw = match.trim()

  if (!raw) {return { error: 'empty', kind: 'domain', payload: '' }}

  if (raw === '*') {return { kind: 'catchAll', payload: '*' }}

  const colon = raw.indexOf(':')
  const kind = colon > 0 ? PREFIXED[raw.slice(0, colon).toLowerCase()] : undefined

  if (kind) {
    const payload = raw.slice(colon + 1)

    if (!payload) {return { error: 'emptyPayload', kind, payload: '' }}

    //: 这两个前缀的值允许带空格（正则里的空格、进程路径里的空格），别的地方空格就是拼错了。
    if (kind !== 'regex' && kind !== 'process' && /\s/.test(payload)) {return { error: 'space', kind, payload }}

    return { kind, payload }
  }

  //: 冒号前不是已知前缀：`example.com:8080` 也会走到这里。规则里端口没有意义，当错报更省事。
  if (colon > 0) {return { error: 'badPrefix', kind: 'domain', payload: raw.slice(colon + 1) }}

  if (/\s/.test(raw)) {return { error: 'space', kind: 'domain', payload: raw }}

  //: `*` 是兜底那一条独占的写法；夹在中间的通配（`api.*.com`）引擎不认。
  if (raw.includes('*') && !raw.startsWith('*.')) {return { error: 'wildcard', kind: 'domain', payload: raw }}

  if (raw.startsWith('*.')) {return { kind: 'suffix', payload: raw.slice(2) }}

  if (raw.includes('/')) {return { kind: 'cidr', payload: raw }}

  return { kind: 'domain', payload: raw }
}

/** 兜底那一条：所有没匹配上的流量都去它那儿，它必须留在最后。 */
export function isCatchAll(rule: NetworkEgressRule): boolean {
  return rule.match.trim() === '*'
}

/**
 * 加一条（同一 match 就地改去向）。
 *
 * 表里两条一模一样的匹配，第二条永远走不到 —— 与其留一条死规则，不如把用户这次点的那一下
 * 当成"改这一条的去向"。
 */
export function patchRuleAdd(block: EgressBlock, match: string, via: string): EgressBlock {
  const target = match.trim()
  const reading = readRule(target)

  if (reading.error) {throw new Error(`rule is not usable: ${reading.error}`)}

  const out = copy(block)
  const rules = out.rules ?? []
  const entry: NetworkEgressRule = { match: target, via }
  const existing = rules.findIndex(rule => rule.match.trim() === target)

  if (existing >= 0) {
    rules[existing] = entry
  } else {
    const last = catchAllIndex(rules)

    if (last >= 0) {rules.splice(last, 0, entry)}
    else {rules.push(entry)}
  }

  out.rules = rules

  return out
}

/** 删一条。兜底那条删不得：删了它，"没匹配上的流量去哪"这个问题就没有答案。 */
export function patchRuleRemove(block: EgressBlock, index: number): EgressBlock {
  const out = copy(block)
  const rules = out.rules ?? []
  const rule = rules[index]

  if (!rule) {throw new Error('no rule at that index')}

  if (isCatchAll(rule)) {throw new Error('the catch-all rule is the table default; it cannot be removed')}

  out.rules = rules.filter((_, at) => at !== index)

  return out
}

/**
 * 上下挪一格。跨不过兜底那条 —— 它一往后挪，排在它后面的规则就永远轮不到。
 * 挪不动时把原来的块原样还回去（屏幕据此什么都不发，而不是发一份没变化的表）。
 */
export function patchRuleMove(block: EgressBlock, index: number, delta: -1 | 1): EgressBlock {
  const out = copy(block)
  const rules = out.rules ?? []
  const rule = rules[index]
  const to = index + delta

  if (!rule || isCatchAll(rule) || to < 0 || to >= rules.length || isCatchAll(rules[to])) {return block}

  const moved = { ...rule }
  rules[index] = rules[to]
  rules[to] = moved

  return out
}

/** 改某一条的去向（直连 / 某个节点）。 */
export function patchRuleVia(block: EgressBlock, index: number, via: string): EgressBlock {
  const out = copy(block)
  const rules = out.rules ?? []

  if (!rules[index]) {throw new Error('no rule at that index')}

  rules[index] = { ...rules[index], via }

  return out
}

/** 表里没有兜底规则时补一条 —— 「默认出口」必须意味着"没匹配上的都去哪"。 */
export function patchRuleCatchAll(block: EgressBlock, via: string): EgressBlock {
  return patchRuleAdd(block, '*', via)
}

export interface SystemProxyReading {
  declared?: boolean
  ours?: boolean
  supported?: boolean
}

/**
 * 「表说要接管」和「注册表现在是我们的」是两件事，四句话不许合并成一句（这台 10-31 实测：
 * 别的程序在我们接管之后自己把注册表改回去了，屏幕上那个开关照旧亮着）。
 *
 * 它是 tooltip 的句子，不是屏幕上的正文——状态用形状说（用户 10-27），话留在悬停里。
 * 句子全部来自 i18n 目录，本模块不自己造词。少一个读数就说没有：没有 `ours` 不等于 `false`，
 * 那会把"读不到"报成"没接管"。
 */
export function proxyTooltip(
  copy: {
    proxyDeclaredNotApplied: string
    proxyNotOurs: string
    proxyOurs: string
    proxyUnsupported: string
  },
  proxy?: SystemProxyReading
): string {
  if (!proxy) {return ''}

  if (!proxy.supported) {return copy.proxyUnsupported}

  if (proxy.ours) {return copy.proxyOurs}

  return proxy.declared ? copy.proxyDeclaredNotApplied : copy.proxyNotOurs
}
