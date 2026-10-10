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

test('the desktop owns none of the four sentences', () => {
  const source = fs.readFileSync(STATUS_SOURCE_PATH, 'utf8')

  for (const [state, sentence] of Object.entries(serverNotes())) {
    assert.ok(
      !source.includes(sentence),
      `chatlog-status.ts 里出现了服务端那句「${state}」的副本 —— 那就是第二真源，本刀要删的正是它`
    )
  }
})

test('spawn uses only the binary the server named, and never while it claims ready', () => {
  assert.equal(spawnableBinary({ state: 'build_missing', note: 'x', binary: 'D:/Tools/chatlog.exe' }), 'D:/Tools/chatlog.exe')
  assert.equal(spawnableBinary({ state: 'build_missing', note: 'x', binary: null }), null)
  assert.equal(spawnableBinary({ state: 'ready', note: null, binary: 'D:/Tools/chatlog.exe' }), null)
  assert.equal(spawnableBinary(NO_ANSWER), null)
})
