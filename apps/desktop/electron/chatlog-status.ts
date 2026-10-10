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
  /** 首启存量审查那道闸的读数（裁定 102 三缓解条）。句子同样来自服务端，桌面不写。 */
  gate?: ReviewGate | null
}

export type ReviewGate = { complete: boolean; gatedTalkers: number; note: string }

/** 台账只认这三档（`deferred-sidecars.ts` 的 `DeferredSubsystemState`）。 */
export type LedgerLevel = 'ready' | 'deferred' | 'red'

const DEAD_STATES = new Set<string>(COLLECTOR_STATES.filter(item => item !== 'ready'))

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
      binary: typeof collector.binary === 'string' ? collector.binary : null,
      gate: readGate(collector.gate)
    }
  }

  return NO_ANSWER
}

/** 闸那位读数只认服务端给的形状；缺了就当"不知道"，不在这里补一个默认。 */
function readGate(raw: unknown): ReviewGate | null {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const gate = raw as Record<string, any>

  if (typeof gate.complete !== 'boolean' || typeof gate.gatedTalkers !== 'number' || typeof gate.note !== 'string') {
    return null
  }

  return { complete: gate.complete, gatedTalkers: gate.gatedTalkers, note: gate.note }
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
    // 采集器活着、但首启审查还没过 ⇒ 一条都不会采。这一格必须说得出"卡在闸上几个"，
    // 不许因为"库里还是空的"就渲染成一切正常（裁定 102 三缓解条）。
    if (answer.gate && !answer.gate.complete && answer.gate.gatedTalkers > 0) {
      return answer.gate.note
        ? { level: 'deferred', body: answer.gate.note }
        : { level: 'deferred', body: `gate reports ${answer.gate.gatedTalkers} talkers but arrived without its sentence` }
    }

    return { level: 'ready', body: 'collector says it is answering on :5030' }
  }

  if (DEAD_STATES.has(answer.state)) {
    if (!answer.note) {
      // 状态认得、句子没送到——这是接线坏了，宁可可红也不替它编一句。
      return { level: 'red', body: `collector state "${answer.state}" arrived without its sentence` }
    }

    return { level: 'red', body: answer.note }
  }

  return { level: 'deferred', body: 'the backend did not answer for the collector (it is not the collector being idle)' }
}

/** spawn 只该用服务端给的那一个路径；`null` 就是"没有产物"，桌面不许再去猜候选清单。 */
export function spawnableBinary(answer: CollectorAnswer): string | null {
  return answer.binary && DEAD_STATES.has(answer.state) ? answer.binary : null
}
