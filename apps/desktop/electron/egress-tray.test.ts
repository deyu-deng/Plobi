/**
 * Tests for electron/egress-tray.ts — `WP-EGRESS-ROUTING` 的常驻可见面（§16 那一格）。
 *
 * Run with: npm run test:desktop:platforms
 *
 * 这一档只许**映射**。所以断言的形状是「服务端给的那句必须逐字到达」，而**不是**
 * 「桌面判得对不对」——判据在 `agent/egress/engine.py`，那边自有测试。底部那条跨语言守卫
 * 钉的是状态字集合：Python 侧加一个状态而这里没跟上 ⇒ 这里必须报「状态未知」，不许当绿。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  EGRESS_ENGINE_STATES,
  egressTooltip,
  egressTrayStrings,
  overlayToneDot,
  readEgressIndicator,
  remoteEgressIndicator,
  TONE_DOT_RGB
} from './egress-tray'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '..', '..', '..')
const ENGINE_PATH = path.join(REPO_ROOT, 'agent', 'egress', 'engine.py')

const zh = egressTrayStrings('zh')
const en = egressTrayStrings('en')

const labels = (indicator: { rows: Array<{ label: string }> }) => indicator.rows.map(row => row.label)

function snapshot(overrides: Record<string, any> = {}) {
  return {
    declared: true,
    unreadable: null,
    rules: [
      { match: 'github.com', via: 'DIRECT', engine_only: false, env_dropped: false },
      { match: 'GEOSITE,category-ai,cn', via: 'POOL', engine_only: true, env_dropped: true }
    ],
    mirrors: { pypi: 'https://mirrors.example.invalid/simple' },
    membership: { nodes: ['hk-1', 'jp-1'], groups: { POOL: ['hk-1', 'jp-1'] } },
    engine: {
      enabled: true,
      state: 'running-ours',
      detail: 'started by Plobi and still running',
      mixed_port: 7898,
      config_source: 'compiled',
      pid: 20064,
      probe: '204 via http://127.0.0.1:7898',
      notes: []
    },
    downgrades: [],
    ...overrides
  }
}

test('没有答案 ≠ 没有声明：拿不到答案时不许折成「未启用」', () => {
  for (const blank of [null, undefined, 42, 'ok', {}]) {
    const indicator = readEgressIndicator(blank, zh)

    assert.equal(indicator.tone, 'unknown', String(blank))
    assert.equal(indicator.badge, false)
    assert.equal(indicator.summary, zh.noAnswer)
    assert.ok(!labels(indicator).includes(zh.notDeclared))
  }
})

test('没有声明表 ⇒ off 且不打点（未启用不该被画成异常）', () => {
  const indicator = readEgressIndicator(snapshot({ declared: false }), zh)

  assert.equal(indicator.tone, 'off')
  assert.equal(indicator.badge, false)
  assert.equal(indicator.summary, zh.notDeclared)
})

test('配置读不出来与没声明是两态，句子取服务端那句', () => {
  const indicator = readEgressIndicator(snapshot({ declared: false, unreadable: 'bad yaml at line 3' }), zh)

  assert.equal(indicator.tone, 'bad')
  assert.equal(indicator.summary, 'network.egress: bad yaml at line 3')
  assert.ok(!labels(indicator).includes(zh.notDeclared), 'unreadable must not fall through to "not declared"')
})

test('running-ours + 打一枪成 ⇒ 绿，并把端口与探针句子原样带上', () => {
  const indicator = readEgressIndicator(snapshot(), zh)

  assert.equal(indicator.tone, 'ok')
  assert.equal(indicator.badge, true)
  assert.ok(indicator.summary.includes(zh.tone.ok), indicator.summary)
  assert.ok(indicator.summary.includes(zh.off) === false)
  assert.deepEqual(labels(indicator).slice(0, 2), [
    '出口正常 · started by Plobi and still running',
    '2 条规则 · 兜底 POOL'
  ])
  assert.ok(labels(indicator).includes('监听 127.0.0.1:7898 · compiled'))
  assert.ok(labels(indicator).includes('打一枪 204 via http://127.0.0.1:7898'))
  assert.ok(labels(indicator).includes('pid 20064'))
  assert.ok(labels(indicator).includes('成员 2 节点 / 1 组 · 只到引擎'))
  assert.ok(labels(indicator).includes('镜像 1 项（按工具配，不改 TLS）'))
  assert.ok(labels(indicator).includes('1 条只在引擎里生效 · 子进程拿不到'))
  assert.ok(labels(indicator).includes('上限：1 条规则装不进子进程的环境变量，那些请求它会直连'))
})

test('打一枪 FAIL ⇒ 红，端口在听也不算正常', () => {
  const indicator = readEgressIndicator(
    snapshot({ engine: { ...snapshot().engine, probe: 'FAIL via http://127.0.0.1:7898: URLError timed out' } }),
    zh
  )

  assert.equal(indicator.tone, 'bad')
  assert.ok(labels(indicator).some(line => line.startsWith('打一枪 FAIL')))
})

test('别人起的引擎：能带流量就算 ok，但归属那句话必须逐字到达', () => {
  const indicator = readEgressIndicator(
    snapshot({
      engine: { ...snapshot().engine, state: 'running-foreign', detail: 'Plobi did not start this engine and will not stop it (pid 31144)' }
    }),
    zh
  )

  assert.equal(indicator.tone, 'ok')
  assert.ok(indicator.summary.endsWith('Plobi did not start this engine and will not stop it (pid 31144)'))
})

test('stopped / failed ⇒ 红；off 或未启用 ⇒ 不画点', () => {
  for (const state of ['stopped', 'failed']) {
    const indicator = readEgressIndicator(snapshot({ engine: { ...snapshot().engine, state, probe: '' } }), zh)

    assert.equal(indicator.tone, 'bad', state)
    assert.equal(indicator.badge, true, state)
  }

  const off = readEgressIndicator(
    snapshot({
      engine: { ...snapshot().engine, enabled: false, state: 'off', probe: '', detail: 'engine projection is switched off in config' }
    }),
    zh
  )

  assert.equal(off.tone, 'off')
  assert.equal(off.badge, false)
  // 句子用服务端那句，只有它没送话时才落到本档的兜底句。
  assert.equal(off.summary, `${zh.tone.off} · engine projection is switched off in config`)

  const mute = readEgressIndicator(snapshot({ engine: { enabled: true, state: '', detail: '', probe: '' } }), zh)

  assert.equal(mute.summary, `${zh.tone.unknown} · ${zh.off}`)
})

test('服务端新增的状态字：报「未知」，不许折成绿', () => {
  const indicator = readEgressIndicator(snapshot({ engine: { ...snapshot().engine, state: 'reloading' } }), zh)

  assert.equal(indicator.tone, 'unknown')
  assert.ok(indicator.summary.includes('reloading'), indicator.summary)
})

test('降级与注意各占一行，内容用服务端的词', () => {
  const indicator = readEgressIndicator(
    snapshot({
      downgrades: [{ exit: 'POOL', reason: 'probe FAIL via 127.0.0.1:7898' }],
      engine: { ...snapshot().engine, notes: ['engine record is unreadable: json'] }
    }),
    zh
  )

  assert.ok(labels(indicator).includes('降级 POOL · probe FAIL via 127.0.0.1:7898'))
  assert.ok(labels(indicator).includes('注意 · engine record is unreadable: json'))
})

test('空规则表说「一条都没有」，不拿 undefined 当兜底出口', () => {
  const indicator = readEgressIndicator(snapshot({ rules: [] }), zh)

  assert.ok(labels(indicator).includes(zh.noRules))
  assert.ok(!labels(indicator).some(line => line.includes('兜底 undefined')))
})

test('tooltip 只到「分组 · 档位」，短到 Windows 不截；远端模式不给本机打点', () => {
  const indicator = readEgressIndicator(snapshot(), zh)

  assert.equal(egressTooltip(zh, indicator), '代理（VPN） · 出口正常')
  assert.ok(egressTooltip(zh, indicator).length < 63, 'Windows clips long tooltips')

  const english = readEgressIndicator(snapshot(), en)

  assert.equal(english.tone, 'ok')
  assert.ok(english.summary.startsWith('egress ok · '))
  assert.ok(labels(english).includes('membership 2 node(s) / 1 group(s) · engine-side'))
  assert.equal(egressTooltip(en, english), 'Proxy (VPN) · egress ok')

  const remote = remoteEgressIndicator(zh)

  assert.equal(remote.badge, false, 'a remote backend must not light this machine\'s icon')
  assert.equal(remote.tone, 'off')
  assert.deepEqual(labels(remote), [zh.remote])
})

test('色点：off 与小图原样返回，输入不被改写，圆外逐字节不变', () => {
  const source = new Uint8Array(16 * 16 * 4).fill(200)

  assert.deepEqual(Array.from(overlayToneDot(source, 16, 16, 'off')), Array.from(source), 'off paints nothing')
  assert.deepEqual(Array.from(overlayToneDot(source, 6, 6, 'ok')), Array.from(source), 'too small to badge')

  const painted = overlayToneDot(source, 16, 16, 'bad')

  assert.deepEqual(Array.from(source), new Array(16 * 16 * 4).fill(200), 'input must not be mutated')

  const [r, g, b] = TONE_DOT_RGB.bad
  const blend = (channel: number) => Math.round(200 * 0.25 + channel * 0.75)

  const at = (x: number, y: number) => {
    const i = (y * 16 + x) * 4

    return [painted[i], painted[i + 1], painted[i + 2], painted[i + 3]]
  }

  // 圆心（右下角往里让一个半径）：BGRA 三通道全被拉向点色，alpha 钉成不透明——
  // 图标那一角本来是透明的，不钉住就看不见这颗点。
  assert.deepEqual(at(11, 11), [blend(b), blend(g), blend(r), 255])
  assert.deepEqual(at(0, 0), [200, 200, 200, 200], 'top-left must stay untouched')
  assert.deepEqual(at(15, 15), [200, 200, 200, 200], 'the dot must not bleed into the corner pixel')

  const changed = painted.filter((byte, index) => byte !== source[index]).length / 4

  assert.ok(changed >= 20 && changed <= 81, `dot covers ${changed}px of a 16px icon — out of range`)
})

test('跨语言守卫：状态字集合与 agent/egress/engine.py 真正交给 EngineReport 的那几个一致', () => {
  const source = fs.readFileSync(ENGINE_PATH, 'utf8')
  const literals = new Map([...source.matchAll(/^([A-Z]{2,}) = "([a-z-]+)"$/gm)].map(match => [match[1], match[2]]))
  const handed = [...new Set([...source.matchAll(/EngineReport\(\s*([A-Z][A-Z_]*)\b/g)].map(match => match[1]))]

  assert.ok(handed.length >= 5, `test fixture: expected engine.py to report at least 5 states, saw ${handed}`)
  assert.deepEqual(
    [...EGRESS_ENGINE_STATES].sort(),
    [...new Set(handed.map(name => literals.get(name)))].sort()
  )
})

test('桌面不判第二遍：本档里不许出现端口号、进程名或「在听」之类的本地判断', () => {
  const text = fs.readFileSync(path.join(HERE, 'egress-tray.ts'), 'utf8')

  for (const forbidden of ['netstat', 'tasklist', 'psutil', 'createConnection', 'mixed_port === 7890', 'engine_port']) {
    assert.ok(!text.includes(forbidden), `egress-tray.ts must not re-derive state locally (${forbidden})`)
  }
})
