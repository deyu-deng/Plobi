/**
 * Tests for electron/chatlog-status.ts — `WP-SIDECAR-STATE-SINGLE-SOURCE` 判据 1/2/3。
 *
 * Run with: npm run test:desktop:platforms
 *
 * Pure mapping: no Electron, no tray, no ports, no probes. The drift guard at the
 * bottom is the cross-language one this knife exists for — the four "缺哪一步"
 * sentences are owned by `plobi/collectors/chatlog/sidecar.py`, so the desktop file
 * must contain **none** of them, and the rendered row must be the sentence handed
 * to it, byte for byte. Change one literal server-side and the desktop line changes
 * with it; re-introduce a local copy and the guard goes red.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  chatlogLedgerRow,
  COLLECTOR_STATES,
  NO_ANSWER,
  readCollectorAnswer,
  spawnableBinary
} from './chatlog-status'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '..', '..', '..')
const SIDECAR_PATH = path.join(REPO_ROOT, 'plobi', 'collectors', 'chatlog', 'sidecar.py')
const STATUS_SOURCE_PATH = path.join(HERE, 'chatlog-status.ts')

/** Read the server's own `_NOTES` — one 口径, two renderings is only honest if we read it. */
function serverNotes(): Record<string, string> {
  const source = fs.readFileSync(SIDECAR_PATH, 'utf8')
  const block = source.match(/_NOTES = \{([\s\S]*?)\n\}/)

  assert.ok(block, 'test fixture: sidecar.py must define `_NOTES`')

  const notes: Record<string, string> = {}

  for (const line of block[1].split('\n')) {
    const entry = line.match(/^\s*STATE_([A-Z_]+):\s*"(.*)",?\s*$/)

    if (entry) {
      notes[entry[1].toLowerCase()] = entry[2]
    }
  }

  return notes
}

test('a 503 answer keeps the server state, its sentence and its binary', () => {
  const answer = readCollectorAnswer(503, {
    ok: false,
    state: 'go_missing',
    error: 'chatlog 采不到：构建工具 Go 没装',
    binary: null
  })

  assert.deepEqual(answer, { state: 'go_missing', note: 'chatlog 采不到：构建工具 Go 没装', binary: null })
})

test('a 200 enumeration is the collector being alive, not an empty board', () => {
  const answer = readCollectorAnswer(200, {
    reviewComplete: true,
    talkers: [],
    collector: { ok: true, state: 'ready', enumerated: 0 }
  })

  assert.equal(answer.state, 'ready')
  assert.equal(chatlogLedgerRow(answer).level, 'ready')
})

test('判据 3 — an empty board without a collector answer is never rendered as fine', () => {
  // 旧形状就是这个洞：200 + 空表看起来和"一切正常"一模一样。
  const answer = readCollectorAnswer(200, { reviewComplete: false, talkers: [] })

  assert.deepEqual(answer, NO_ANSWER)

  const row = chatlogLedgerRow(answer)

  assert.equal(row.level, 'deferred')
  assert.ok(!row.body.includes('ready'), '后端没答时不许写成"好了"')
})

test('an unrecognised status code is no answer, not a guess', () => {
  assert.deepEqual(readCollectorAnswer(500, { detail: 'boom' }), NO_ANSWER)
  assert.deepEqual(readCollectorAnswer(200, null), NO_ANSWER)
  assert.deepEqual(readCollectorAnswer(503, { state: 'go_missing' }), NO_ANSWER)
})

test('判据 2 的对照 — the rendered line is the server sentence, byte for byte', () => {
  const notes = serverNotes()

  assert.ok(Object.keys(notes).length >= 4, 'test fixture: expected the four dead-end sentences plus ready')

  for (const state of COLLECTOR_STATES) {
    if (state === 'ready') {
      continue
    }

    const sentence = `chatlog 采不到：${notes[state]}`
    const row = chatlogLedgerRow({ state, note: sentence, binary: null })

    assert.equal(row.level, 'red', `${state} must be shown as a fault, not postponed`)
    assert.equal(row.body, sentence, `${state}: 桌面改写了服务端那句话`)

    // 服务端把那个字面改掉，桌面这行必须跟着变（同一个引用，没有副本可滞后）。
    const doctored = sentence.replace('采不到', '采不到【改了个字面】')

    assert.equal(chatlogLedgerRow({ state, note: doctored, binary: null }).body, doctored)
  }
})

test('判据 1 — a known state that arrives without its sentence goes red, never invents one', () => {
  const row = chatlogLedgerRow({ state: 'key_missing', note: null, binary: null })

  assert.equal(row.level, 'red')
  assert.ok(!row.body.includes('Go'), '不许替它猜一句别的原因')
})

test('spawn uses only the binary the server named, and never while it claims ready', () => {
  assert.equal(spawnableBinary({ state: 'build_missing', note: 'x', binary: 'D:/Tools/chatlog.exe' }), 'D:/Tools/chatlog.exe')
  assert.equal(spawnableBinary({ state: 'build_missing', note: 'x', binary: null }), null)
  assert.equal(spawnableBinary({ state: 'ready', note: null, binary: 'D:/Tools/chatlog.exe' }), null)
  assert.equal(spawnableBinary(NO_ANSWER), null)
})

test('裁定 109 — the retired first-run gate leaves no never-firing sentence behind', () => {
  const desktop = fs.readFileSync(STATUS_SOURCE_PATH, 'utf8')
  const main = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf8')
  const pipeline = fs.readFileSync(path.join(REPO_ROOT, 'plobi', 'collectors', 'chatlog', 'pipeline.py'), 'utf8')

  // 闸由用户本人退役 ⇒ 那句"卡在审查闸上 N 个会话"永远不会有触发路径。
  // 留着它当"可见性"正是要避免的形状，所以三处源码里都不许再出现它。
  for (const [name, source] of [['chatlog-status.ts', desktop], ['main.ts', main], ['pipeline.py', pipeline]] as const) {
    assert.ok(!source.includes('REVIEW_GATE_NOTE'), `${name} 还引用着已退役的闸门句子`)
    assert.ok(!source.includes('首启存量审查还没做完'), `${name} 还留着那句永不触发的话`)
    assert.ok(!source.includes('review_gated'), `${name} 还在读已删的 report.review_gated`)
  }

  // 退役之后"枚举到就扫"，ready 就是一句 ready，不再被闸改色。
  const ready = readCollectorAnswer(200, {
    reviewComplete: false,
    talkers: [],
    collector: { ok: true, state: 'ready', enumerated: 0 }
  })

  assert.equal(chatlogLedgerRow(ready).level, 'ready')
})

test('the desktop owns none of the four states', () => {
  const source = fs.readFileSync(STATUS_SOURCE_PATH, 'utf8')

  for (const [state, sentence] of Object.entries(serverNotes())) {
    assert.ok(!source.includes(sentence), `chatlog-status.ts 里出现了服务端那句「${state}」的副本`)
  }

})

/**
 * 判据 1 的另一半：那套自判**删掉了，而且要防止它回来**。
 * 每条都对 `main.ts` 的真实源码断言，不是对我脑子里的记述断言。
 */
test('main.ts no longer judges the collector a second time — and may not start again', () => {
  const main = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf8')
  const code = main
    .split('\n')
    .filter(line => !line.trim().startsWith('*') && !line.trim().startsWith('//') && !line.trim().startsWith('/*'))
    .join('\n')

  const forbidden: Array<[string, string]> = [
    ['no chatlog binary on this machine', '桌面自己拼的"没产物"文案——那句归服务端'],
    ["'D:', 'Tools', 'wechat'", '硬编的第二份产物候选（与 Python 的 binary_candidates 并存）'],
    ["'tools', 'chatlog', 'bin'", '同上，仓内那条候选'],
    ["'/IM', 'chatlog.exe'", '按映像名全局 taskkill：杀了谁、谁起的都不知道'],
    ['async function chatlogHealthy', '第二条 :5030 探活（服务端已有 chatlog_is_healthy）'],
    ['function resolveChatlogBinary', '桌面侧的候选解析函数本体']
  ]

  for (const [needle, why] of forbidden) {
    assert.ok(!code.includes(needle), `main.ts 里又出现了「${needle}」：${why}`)
  }

  // 消费端必须在：状态与句子都从 chatlog-status 那一层拿。
  assert.ok(code.includes("from './chatlog-status'"), 'main.ts 不再消费 chatlog-status ⇒ 它多半自己判回去了')
  assert.ok(code.includes('chatlogCollectorAnswer'), '台账那一行必须走服务端答案')
})

/**
 * 裁定 105 补的三条杀进程边界——可机器验的那两条：
 * 只杀自己那个子进程；拿不到就"只报不清"，且清单要在动手之前取。
 */
test('stray cleanup kills only its own child and reports what it left alone', () => {
  const main = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf8')

  assert.ok(main.includes('IMAGENAME eq chatlog.exe'), '杀前必须先取 chatlog.exe 的 PID 清单')
  assert.ok(main.includes('pids.filter(pid => pid === owned)'), '只允许杀自己起的那一个')
  assert.ok(main.includes('left alone'), '拿不到自己的子进程 PID 时要"只报不清"，并且这条要看得见')
  assert.ok(!main.includes("'/IM', 'chatlog.exe'"), '按映像名全局杀已经作废，不许回来')
})
