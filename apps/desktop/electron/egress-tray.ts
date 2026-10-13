/**
 * 托盘上那颗出口指示 — `WP-EGRESS-ROUTING` 的常驻可见面（用户 10-13 09:2x 拍「托盘/状态条常驻指示」）。
 *
 * 规矩与 `chatlog-status.ts` 同一条：**这一档只映射，不判断**。出口状态的唯一出处是
 * `agent/egress/status.py` 的 `snapshot()`（`plobi network status` 与设置面板读的都是它），
 * 桌面这里只把那份答案翻译成人话 + 一个颜色，一个字都不自己判（裁定 94 / 102）。
 * 拿不到答案时不许折成「没配」，也不许当「正常」——那正是这条口径点名的病。
 *
 * 本模块不 import electron：像素与文案都是纯函数，node:test 直接跑（`test:desktop:platforms`）。
 */

export type EgressTone = 'ok' | 'bad' | 'off' | 'unknown'

export type EgressRow = { label: string; toolTip: string }

export type EgressIndicator = {
  tone: EgressTone
  /** 只有「用户真声明了表并且开了引擎」才给图标打点；没启用不该被画成异常。 */
  badge: boolean
  summary: string
  rows: EgressRow[]
}

/** `agent/egress/engine.py` 那一组状态字的现读镜像（判据：见 `test_egress_engine.py`）。 */
export const EGRESS_ENGINE_STATES = ['off', 'running-ours', 'running-foreign', 'stopped', 'failed'] as const

/** 服务端把「打一枪成/败」编码在这个前缀里，桌面只认它，不另判一次。 */
const FAIL_PREFIX = 'FAIL'

/** 两语言共用一个形状：加一句就得两边都加，否则这里先红，不会到了托盘才发现。 */
export type EgressTrayStrings = {
  group: string
  tone: Record<EgressTone, string>
  noAnswer: string
  remote: string
  notDeclared: string
  off: string
  table: (rules: number, via: string) => string
  noRules: string
  engineOnly: (n: number) => string
  ceiling: (n: number) => string
  members: (nodes: number, groups: number) => string
  listen: (port: number, source: string) => string
  pid: (pid: number) => string
  probe: (text: string) => string
  note: (text: string) => string
  downgrade: (exit: string, reason: string) => string
  mirrors: (n: number) => string
}

export const EGRESS_STRINGS: Record<'zh' | 'en', EgressTrayStrings> = {
  zh: {
    group: '代理（VPN）',
    tone: { ok: '出口正常', bad: '出口异常', off: '未启用', unknown: '状态未知' },
    noAnswer: '后端没有回答出口状态（不是「没配」，是没答）',
    remote: '后端在另一台机器上：这里的出口状态说的不是本机',
    notDeclared: '没有声明出口表 · 流量按环境里的代理走',
    off: '引擎投影在配置里是关的',
    table: (rules: number, via: string) => `${rules} 条规则 · 兜底 ${via}`,
    noRules: '表里一条规则都没有',
    engineOnly: (n: number) => `${n} 条只在引擎里生效 · 子进程拿不到`,
    ceiling: (n: number) => `上限：${n} 条规则装不进子进程的环境变量，那些请求会走兜底出口`,
    members: (nodes: number, groups: number) => `成员 ${nodes} 节点 / ${groups} 组 · 只到引擎`,
    listen: (port: number, source: string) => `监听 127.0.0.1:${port} · ${source || 'no config'}`,
    pid: (pid: number) => `pid ${pid}`,
    probe: (text: string) => `打一枪 ${text}`,
    note: (text: string) => `注意 · ${text}`,
    downgrade: (exit: string, reason: string) => `降级 ${exit} · ${reason}`,
    mirrors: (n: number) => `镜像 ${n} 项（按工具配，不改 TLS）`
  },
  en: {
    group: 'Proxy (VPN)',
    tone: { ok: 'egress ok', bad: 'egress broken', off: 'not enabled', unknown: 'state unknown' },
    noAnswer: 'the backend did not answer for egress (not "unset" — no answer)',
    remote: 'the backend runs on another machine: this box\'s egress is not what it reports',
    notDeclared: 'no egress table declared · traffic follows the inherited environment',
    off: 'the engine projection is switched off in config',
    table: (rules: number, via: string) => `${rules} rule(s) · catch-all ${via}`,
    noRules: 'the table holds no rule at all',
    engineOnly: (n: number) => `${n} rule(s) only take effect inside the engine · a spawned child cannot see them`,
    ceiling: (n: number) => `ceiling: ${n} rule(s) cannot be expressed in a spawned child's env — those follow the catch-all`,
    members: (nodes: number, groups: number) => `membership ${nodes} node(s) / ${groups} group(s) · engine-side`,
    listen: (port: number, source: string) => `listening 127.0.0.1:${port} · ${source || 'no config'}`,
    pid: (pid: number) => `pid ${pid}`,
    probe: (text: string) => `probe ${text}`,
    note: (text: string) => `note · ${text}`,
    downgrade: (exit: string, reason: string) => `downgrade ${exit} · ${reason}`,
    mirrors: (n: number) => `${n} mirror(s) (per tool, TLS untouched)`
  }
}

export function egressTrayStrings(language: 'zh' | 'en'): EgressTrayStrings {
  return EGRESS_STRINGS[language]
}

const NO_ANSWER: EgressIndicator = { tone: 'unknown', badge: false, summary: '', rows: [] }

/**
 * 把一次 `/api/network/status` 的响应读成托盘那一份指示。
 *
 * `answer` 为 `null`（后端没答 / 还在启动 / 在远端模式下不该本机打点）时给 `noAnswer`，
 * **不**折成「未启用」。状态字不在已知集合里 ⇒ `unknown` 且不打点：桌面不许比服务端先乐观。
 */
export function readEgressIndicator(answer: unknown, strings: EgressTrayStrings): EgressIndicator {
  if (!answer || typeof answer !== 'object') {
    return { ...NO_ANSWER, summary: strings.noAnswer, rows: [{ label: strings.noAnswer, toolTip: strings.noAnswer }] }
  }

  const payload = answer as Record<string, any>

  // 一份没有 `declared` 的响应不是一份答案：它更像是接口挂在半路。这里不许替它猜「没配」。
  if (typeof payload.declared !== 'boolean') {
    return { ...NO_ANSWER, summary: strings.noAnswer, rows: [{ label: strings.noAnswer, toolTip: strings.noAnswer }] }
  }

  const engine = payload.engine && typeof payload.engine === 'object' ? (payload.engine as Record<string, any>) : null
  const rows: EgressRow[] = []

  const push = (label: string, toolTip = label) => {
    rows.push({ label, toolTip })
  }

  if (payload.unreadable) {
    // 「配置读不出来」与「没声明」是两态（`Table.unreadable`），托盘也不许把它们混成一句。
    const line = `network.egress: ${payload.unreadable}`

    return { tone: 'bad', badge: true, summary: line, rows: [{ label: line, toolTip: line }] }
  }

  if (payload.declared !== true) {
    return {
      tone: 'off',
      badge: false,
      summary: strings.notDeclared,
      rows: [{ label: strings.notDeclared, toolTip: strings.notDeclared }]
    }
  }

  const state = typeof engine?.state === 'string' ? engine.state : ''
  const probe = typeof engine?.probe === 'string' ? engine.probe : ''
  const port = Number.isInteger(engine?.mixed_port) ? (engine.mixed_port as number) : 0
  const enabled = engine?.enabled === true

  const rules = Array.isArray(payload.rules) ? payload.rules : []
  const engineOnly = rules.filter((rule: any) => rule?.engine_only === true).length
  const dropped = rules.filter((rule: any) => rule?.env_dropped === true).length
  const membership = payload.membership || {}
  const nodes = Array.isArray(membership.nodes) ? membership.nodes.length : 0
  const groups = membership.groups && typeof membership.groups === 'object' ? Object.keys(membership.groups).length : 0
  const mirrors = payload.mirrors && typeof payload.mirrors === 'object' ? Object.keys(payload.mirrors).length : 0
  const notes = Array.isArray(engine?.notes) ? engine.notes : []
  const downgrades = Array.isArray(payload.downgrades) ? payload.downgrades : []

  let tone: EgressTone

  if (!enabled || state === 'off') {
    tone = 'off'
  } else if (state === 'running-ours' || state === 'running-foreign') {
    tone = probe.startsWith(FAIL_PREFIX) ? 'bad' : 'ok'
  } else if (state === 'stopped' || state === 'failed') {
    tone = 'bad'
  } else {
    tone = 'unknown'
  }

  const detail = typeof engine?.detail === 'string' && engine.detail ? engine.detail : strings.off
  const summary = tone === 'unknown' ? `${strings.tone.unknown} · ${state || detail}` : `${strings.tone[tone]} · ${detail}`

  push(summary, [typeof engine?.state === 'string' ? engine.state : '-', detail].join(' — '))
  push(rules.length ? strings.table(rules.length, String(rules[rules.length - 1]?.via ?? '-')) : strings.noRules)

  if (port > 0) {
    push(strings.listen(port, typeof engine?.config_source === 'string' ? engine.config_source : ''))
  }

  if (probe) {
    push(strings.probe(probe))
  }

  if (Number.isInteger(engine?.pid) && engine.pid > 0) {
    push(strings.pid(engine.pid as number))
  }

  if (nodes || groups) {
    push(strings.members(nodes, groups))
  }

  if (mirrors) {
    push(strings.mirrors(mirrors))
  }

  if (engineOnly) {
    push(strings.engineOnly(engineOnly))
  }

  if (dropped) {
    push(strings.ceiling(dropped))
  }

  for (const note of notes) {
    push(strings.note(String(note)))
  }

  for (const item of downgrades) {
    push(strings.downgrade(String(item?.exit ?? '-'), String(item?.reason ?? '-')))
  }

  // 没启用、或拿不到答案的时候不打点：一颗灰点会把「这台没用这功能」画成「这功能坏了」。
  return { tone, badge: enabled && state !== 'off', summary, rows }
}

/** 托盘 tooltip：短到 Windows 不截断（它的 tooltip 长度上限远低于菜单行）。 */
export function egressTooltip(strings: EgressTrayStrings, indicator: EgressIndicator): string {
  return `${strings.group} · ${strings.tone[indicator.tone]}`
}

/**
 * 远端模式：后端在另一台机器上，它答的出口状态说的不是本机。
 *
 * 这时候**不许**给本机图标打点——一颗绿点会把「另一台机器能出去」画成「这台能出去」，
 * 而那正是这条指示要防的病。
 */
export function remoteEgressIndicator(strings: EgressTrayStrings): EgressIndicator {
  return { tone: 'off', badge: false, summary: strings.remote, rows: [{ label: strings.remote, toolTip: strings.remote }] }
}

/** 点色只区分「能用 / 不能用 / 不知道」，灰色永远不出场——那是「不画」的理由。 */
export const TONE_DOT_RGB: Record<Exclude<EgressTone, 'off'>, [number, number, number]> = {
  ok: [46, 178, 92],
  bad: [222, 64, 54],
  unknown: [232, 160, 44]
}

/**
 * 在 16px 托盘图标的位图（Electron `toBitmap()` 给的 BGRA）右下角叠一颗半透明色点。
 *
 * 纯函数、返回副本：不认得 electron，也就不需要真起一个窗口才能验它。
 * `off` 与太小的图一律原样返回——未启用不该被画成异常。
 */
export function overlayToneDot(
  bitmap: Uint8Array,
  width: number,
  height: number,
  tone: EgressTone
): Uint8Array {
  const out = Uint8Array.from(bitmap)

  if (tone === 'off' || width < 8 || height < 8) {
    return out
  }

  const [r, g, b] = TONE_DOT_RGB[tone]
  // 0.2 ⇒ 16px 图标上是一颗 7px 的小点：再大就盖住脸，读起来像贴纸不像角标。
  const radius = Math.max(2, Math.round(Math.min(width, height) * 0.2))
  const cx = width - radius - 1
  const cy = height - radius - 1

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const dx = x - cx
      const dy = y - cy

      if (dx * dx + dy * dy > radius * radius) {
        continue
      }

      const i = (y * width + x) * 4

      out[i] = Math.round(out[i] * 0.25 + b * 0.75)
      out[i + 1] = Math.round(out[i + 1] * 0.25 + g * 0.75)
      out[i + 2] = Math.round(out[i + 2] * 0.25 + r * 0.75)
      out[i + 3] = 255
    }
  }

  return out
}
