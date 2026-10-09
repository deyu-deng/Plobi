/**
 * Tests for electron/aigw-key-reconcile.ts  +  the cross-language parity table.
 *
 * Run with: node --test electron/aigw-key-reconcile.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * Two jobs. The first is the ordinary one: what the reconciler reports. The
 * second is 裁定 94's: this TS parser and `plobi/agents/registry.py`'s
 * `gateway_declared_api_key()` are two parsers for one file, so they are pinned
 * to a single byte-for-byte table. Python's side carries the same table in
 * `tests/plobi/test_aigw_key_parse_parity.py`. Deliberate cost, accepted by
 * 裁定 94: change Python's parsing semantics without updating both tables and a
 * test goes red -- that is the point, not a tax.
 *
 * Four rows below are the drifts 裁定 94 measured (and this file re-measured on
 * 2026-10-08 against the real Python function): a lowercase placeholder name, a
 * default containing `}`, doubled quotes, and an empty value. Each is a
 * legitimately-shaped config that used to produce a phantom mismatch.
 *
 * Also pinned: nothing this module returns carries key material -- the summary
 * goes into the log ring, and the log ring ships inside support bundles.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  DEV_DEFAULT_API_KEY,
  ENV_ORDER,
  declaredGatewayApiKey,
  fingerprintKey,
  reconcileGatewayKey,
  usedGatewayKey,
} from './aigw-key-reconcile'
import { KEY_RECONCILE_ROW_ID, createDeferredSubsystemLedger } from './deferred-sidecars'

const DECLARED = 'sk-local-dev-key'
const OTHER = 'a-value-that-is-not-the-declared-one'

const configWith = (key: string) =>
  `server:\n  host: 127.0.0.1\n  port: 8000\n  api_key: ${key}\n\nlogging:\n  level: INFO\n`

/**
 * Measured against `gateway_declared_api_key()` on this machine, not copied from
 * a comment. `''` is Python's "declares nothing usable" (missing file, missing
 * block, missing key and an empty scalar all collapse to it, and the caller then
 * falls back to `AIGW_DEV_DEFAULT_API_KEY`).
 */
const PARITY: Array<[string, string, Record<string, string>, string]> = [
  ['placeholder-caps', '${AIGW_KEY:-sk-from-placeholder}', {}, 'sk-from-placeholder'],
  ['placeholder-lower', '${aigw_key:-X}', {}, '${aigw_key:-X}'],
  ['brace-in-default', '${AIGW_KEY:-a}b}', {}, '${AIGW_KEY:-a}b}'],
  ['double-quoted', "''k''", {}, 'k'],
  ['empty-string', '""', {}, ''],
  ['plain', 'sk-local-dev-key', {}, 'sk-local-dev-key'],
  ['placeholder-env-set', '${AIGW_KEY:-fallback}', { AIGW_KEY: 'from-env' }, 'from-env'],
  ['trailing-comment', `${DECLARED}  # rotated 2026-10-08`, {}, DECLARED],
]

// Structural shapes carry whole files rather than a scalar: Python returns `''`
// for "no api_key line", "no server: block", an empty file and even a missing
// file, all indistinguishable, and its caller then uses the shared default.
const PARITY_WHOLE_FILE: Array<[string, string, string]> = [
  ['no-api-key-line', 'server:\n  port: 8000\n', ''],
  ['no-server-block', 'logging:\n  level: INFO\n', ''],
  ['empty-file', '', ''],
]

for (const [name, scalar, env, expected] of PARITY) {
  test(`parity ${name}: TS parses byte-identically to the Python reader`, () => {
    assert.equal(declaredGatewayApiKey(configWith(scalar), env as NodeJS.ProcessEnv), expected)
  })
}

for (const [name, text, expected] of PARITY_WHOLE_FILE) {
  test(`parity ${name}: TS parses byte-identically to the Python reader`, () => {
    assert.equal(declaredGatewayApiKey(text, {}), expected)
  })
}

test('one deliberate difference stays documented: an unreadable file is NOT CHECKED in TS', () => {
  // Python's reader swallows the OSError and returns '' like any other
  // "declares nothing"; the shell knows whether the file is there, and
  // `main.ts` already reports a missing gateway config as a deferred sidecar.
  // So "we could not look" is kept distinct from "it declares nothing".
  assert.equal(declaredGatewayApiKey(null, {}), null)
  assert.equal(declaredGatewayApiKey('', {}), '')
})

test('reconcile passes when the shell fallback equals what the gateway declares', () => {
  const r = reconcileGatewayKey(configWith(DECLARED), {})
  assert.equal(r.match, true)
  assert.equal(r.usedSource, 'development default')
  assert.equal(r.declaredFp, fingerprintKey(DECLARED))
})

test('an empty declared value is "no declaration", not a mismatch (裁定 94 item 2)', () => {
  // `api_key: ""` is a valid config. Python resolves it to the shared
  // development default; treating it as a declared-but-empty string used to
  // fingerprint against '' and report a red that no user caused.
  const r = reconcileGatewayKey(configWith('""'), {})
  assert.equal(r.declaredReadable, true)
  assert.equal(r.match, true)
  assert.equal(r.declaredFp, fingerprintKey(DEV_DEFAULT_API_KEY))
})

test('reconcile goes red when the environment carries a different key', () => {
  const r = reconcileGatewayKey(configWith(DECLARED), { AIGW_API_KEY: OTHER })
  assert.equal(r.match, false)
  assert.equal(r.usedSource, 'env:AIGW_API_KEY')
  assert.match(r.summary, /MISMATCH/)
})

test('an unreadable config is NOT CHECKED, never a pass, and never a fake mismatch', () => {
  const r = reconcileGatewayKey(null, {})
  assert.equal(r.match, false)
  assert.equal(r.declaredReadable, false)
  assert.match(r.summary, /NOT CHECKED/)
})

test('no field returned by reconcile carries key material', () => {
  const dump = JSON.stringify(reconcileGatewayKey(configWith(DECLARED), { AIGW_API_KEY: OTHER }))
  assert.ok(!dump.includes(DECLARED), 'summary leaked the declared key')
  assert.ok(!dump.includes(OTHER), 'summary leaked the shell key')
  assert.ok(dump.includes(fingerprintKey(OTHER)), 'fingerprints are the point of the report')
})

test('the shell ladder keeps AIGW_API_KEY ahead of PLOBI_AIGW_API_KEY', () => {
  assert.equal(usedGatewayKey({ AIGW_API_KEY: 'first', PLOBI_AIGW_API_KEY: 'second' }).source, 'env:AIGW_API_KEY')
  assert.equal(usedGatewayKey({ PLOBI_AIGW_API_KEY: 'second' }).source, 'env:PLOBI_AIGW_API_KEY')
})

// ── 镜像钉住（裁定 94 第 1 条后半）：main.ts 那份手抄必须与本模块同字面量 ──────────
const mainTs = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'main.ts'), 'utf-8')

test('main.ts productQuotaApiKey() is still the ladder this module mirrors', () => {
  const wanted = `process.env.${ENV_ORDER[0]} || process.env.${ENV_ORDER[1]} || '${DEV_DEFAULT_API_KEY}'`
  assert.ok(mainTs.includes(wanted), `main.ts 的取值阶梯与本模块不同：要找「${wanted}」`)
})

// ── 已知分叉，钉住而不是悄悄抹平（要合并就两处一起改，本行同步更新） ────────────────
test('known divergence: the Python ladder names a different second variable', () => {
  // registry.py `AIGW_KEY_ENV_VARS` = ("AIGW_API_KEY", "PLOBI_QUOTA_AIGW_KEY"),
  // while the shell's second rung is PLOBI_AIGW_API_KEY. Reported to 治理线 rather
  // than fixed here: quietly changing which variable the shell honours would move
  // behaviour under a user who set one of them.
  assert.equal(ENV_ORDER[1], 'PLOBI_AIGW_API_KEY')
  assert.ok(
    mainTs.includes('PLOBI_AIGW_API_KEY'),
    'main.ts 不再认 PLOBI_AIGW_API_KEY —— 那就与本表与 Python 名单一起统一，别只改一边',
  )
})

// ── 裁定 94 ③ 的牙：那条红必须真能落到面上（本文件 10-09 实测补的） ────────────────
// `reportDeferredSubsystem()` returns null and keeps nothing when the id has no
// definition — so "main.ts calls it" proves nothing on its own. Measured on
// 2026-10-09 while rebuilding the app for the owner: the call was live, the row
// was silently dropped, and the tray/boot surfaces stayed exactly as green as
// they had been. These rows pin the ledger accepts the id, and that it still
// refuses one nobody declared.
test('the ledger accepts the key-check id, so a red actually materialises', () => {
  const ledger = createDeferredSubsystemLedger()

  assert.ok(
    !ledger.rows().some(row => row.id === KEY_RECONCILE_ROW_ID),
    '这一格由第一次真实观察建行，不在启动时就挂一条 "probing"（它没人探）',
  )

  const row = ledger.record(KEY_RECONCILE_ROW_ID, {
    observed: 'MISMATCH — sent fp 9f3a1c22, gateway declares fp 4c1e07aa',
    state: 'red',
  })

  assert.ok(row, 'record() 丢掉了这个 id ⇒ reportDeferredSubsystem() 无处可渲染，那条红就只活在日志里')
  assert.equal(row.id, KEY_RECONCILE_ROW_ID)
  assert.equal(row.port, 0, '这一格没有端口；渲染侧按非正端口省略，不许印成 ":0"')
  assert.match(row.detail, /RED — real fault/, '不许被读成 R-047 那种"故意推迟"')
  assert.match(row.detail, /server\.api_key/, 'detail 要指名改哪一处，不然用户看到了红却不知道动哪')
  assert.ok(!JSON.stringify(row).includes(DEV_DEFAULT_API_KEY), '面上只准出指纹与变量名，key 材料一行都不许进')
})

test('an id nobody declared is still refused — registering one must not open the door', () => {
  const ledger = createDeferredSubsystemLedger()

  assert.equal(ledger.record('totally-unregistered-subsystem', { observed: 'x', state: 'red' }), null)
  assert.equal(ledger.rows().some(row => row.id === 'totally-unregistered-subsystem'), false)
})

test('main.ts reports under the exported id, not a hand-typed string', () => {
  // A literal here could drift from the ledger's registered id and the red would
  // go back to being dropped — which is precisely the failure these rows exist for.
  assert.ok(
    /reportDeferredSubsystem\(\s*KEY_RECONCILE_ROW_ID\b/.test(mainTs),
    "main.ts 必须用 ./deferred-sidecars 导出的那个 id 常量上报，别再手写字符串",
  )
  assert.ok(
    !/reportDeferredSubsystem\(\s*['"]aigw-key['"]\s*,/.test(mainTs),
    'main.ts 里又写回字面量了：常量和 ledger 的登记表可能不同步',
  )
})
