/**
 * chatlog 采集器状态 — 桌面侧唯一的消费点（`WP-SIDECAR-STATE-SINGLE-SOURCE` 判据 1）。
 *
 * 规矩：这四句"缺哪一步"只有一个出处，就是服务端 `plobi/collectors/chatlog/sidecar.py` 算出来的
 * `state` + 那句话。桌面**不再自己看进程**——原来它在 `main.ts` 里硬编产物候选、另读
 * `~/.chatlog/chatlog.json`、再拼一句 "no chatlog binary on this machine (checked …)"，
 * 那是与 Python 侧并存的第二真源（裁定 94 同族）。本模块只做**映射**，不做**判断**：
 * 它把后端已经给出的答案翻译成台账那三档，一个字都不重写。
 */

export const COLLECTOR_STATES = ['ready', 'go_missing', 'build_missing', 'key_missing', 'not_listening'] as const

export type CollectorState = (typeof COLLECTOR_STATES)[number]

/** 后端那句"为什么"要原样到达用户眼前，所以这里保留字符串本体而不是枚举它。 */
export type CollectorAnswer = {
  state: string
  note: string | null
  binary: string | null
}

/** 台账只认这三档（`deferred-sidecars.ts` 的 `DeferredSubsystemState`）。 */
export type LedgerLevel = 'ready' | 'deferred' | 'red'

const DEAD_STATES = new Set<string>(COLLECTOR_STATES.filter(item => item !== 'ready'))

/**
 * 哪些状态是「**本机压根起不来**」——按台账自己那条规则 2（`deferred-sidecars.ts` 头部
 * 引的那句）：「这台机器上压根没有可启动的东西」（没有产物、没有密钥）落 `deferred`，
 * 「**试过了起不来**」才落 `red`。
 *
 * 11-08 改：从前这四档一律 `red`，于是**全新装机、什么都没配**的机器上弹一块
 * 「Service fault · 微信消息采集」——用户 11-08 的原话是「用户明明什么都没有配置……
 * 甚至微信还一直在报错」。那不是故障，那是**还没开始**：用户 11-05 早就说过
 * 「用户没有配置微信。就不采集。相关的采集腿也不要反复报错了」。
 */
const NOT_STARTED_STATES = new Set<string>(['build_missing', 'go_missing', 'key_missing'])

/**
 * 试过了起不来 —— **真故障**，红。
 *
 * `not_listening` 是唯一这一档：它的前置三步都齐了（产物在、密钥在），只是服务没在听。
 * 前三步缺任何一样都是「还没开始」，不是「坏了」。
 */
const ATTEMPTED_STATES = new Set<string>(['not_listening'])

/** 拿不到答案时不许猜成 ready，也不许编一句"大概是没装"——那正是本刀要删的东西。 */
export const NO_ANSWER: CollectorAnswer = {
  state: 'no_answer',
  note: null,
  binary: null
}

/**
 * 把一次 `/api/collect/talkers` 的响应读成一条答案。
 *
 * 200 = 后端枚举成功 ⇒ 采集器活着（`collector.state`）。
 * 503 = 到不了，body 里带 `state` + `error` + `binary`（判据 2 那一笔定的形状）。
 * 其它一律 `no_answer`：不许在这里补判断。
 */
export function readCollectorAnswer(statusCode: number, body: unknown): CollectorAnswer {
  if (!body || typeof body !== 'object') {
    return NO_ANSWER
  }

  const payload = body as Record<string, any>

  if (statusCode === 503) {
    if (typeof payload.state !== 'string' || typeof payload.error !== 'string') {
      return NO_ANSWER
    }

    return { state: payload.state, note: payload.error, binary: typeof payload.binary === 'string' ? payload.binary : null }
  }

  const collector = payload.collector

  if (statusCode === 200 && collector && typeof collector.state === 'string') {
    return {
      state: collector.state,
      note: null,
      binary: typeof collector.binary === 'string' ? collector.binary : null
    }
  }

  return NO_ANSWER
}

/**
 * 台账那一行的人话。
 *
 * ⚠️ 这里**不许**出现任何"缺哪一步"的文案：`body` 逐字用服务端那句（判据 2 的对照测试就钉这个——
 * 服务端改一个字面，这一行必须跟着变）。只有 ready 与"后端没答"两种情况才由本模块给话，
 * 因为它们都不涉及"缺哪一步"的判断。
 */
export function chatlogLedgerRow(answer: CollectorAnswer): { level: LedgerLevel; body: string } {
  if (answer.state === 'ready') {
    return { level: 'ready', body: '微信消息采集服务已就绪' }
  }

  if (ATTEMPTED_STATES.has(answer.state)) {
    if (!answer.note) {
      // 状态认得、句子没送到——这是接线坏了，宁可可红也不替它编一句。
      return { level: 'red', body: `collector state "${answer.state}" arrived without its sentence` }
    }

    return { level: 'red', body: answer.note }
  }

  if (NOT_STARTED_STATES.has(answer.state)) {
    if (!answer.note) {
      return { level: 'deferred', body: `collector state "${answer.state}" arrived without its sentence` }
    }

    // **逐字用服务端那句**（判据 2 的对照测试就钉这个）：这一档说的是「还差哪一步」，
    // 不是「出了什么故障」，所以它归 deferred 那档，不弹红牌。
    return { level: 'deferred', body: answer.note }
  }

  return { level: 'deferred', body: '暂时无法获取微信采集服务状态' }
}

/** spawn 只该用服务端给的那一个路径；`null` 就是"没有产物"，桌面不许再去猜候选清单。 */
export function spawnableBinary(answer: CollectorAnswer): string | null {
  return answer.binary && DEAD_STATES.has(answer.state) ? answer.binary : null
}
