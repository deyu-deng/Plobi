import assert from 'node:assert/strict'
import test from 'node:test'

import { applyDesktopProxy, describeDesktopProxy, desktopProxyConfig, type DesktopProxyView } from './egress-proxy'

const TAKEN_OVER: DesktopProxyView = {
  bypass: ['localhost', '10.0.0.0/8', 'github.com'],
  declared: true,
  mode: 'engine',
  note: '',
  proxy: 'http://127.0.0.1:7895',
  unexpressible: ['geosite:streaming']
}

test('the desktop is told the table in Chromium form, and a lane that cannot take it is named', async () => {
  const config = desktopProxyConfig(TAKEN_OVER)

  assert.deepEqual(config, {
    mode: 'fixed_servers',
    proxyBypassRules: 'localhost;10.0.0.0/8;github.com',
    proxyRules: 'http://127.0.0.1:7895'
  })
  // CIDR 在 Chromium 里说得出来 —— WinINET 那格表达不了的不能在这里被悄悄丢掉
  assert.ok(config.proxyBypassRules?.includes('10.0.0.0/8'))
  const line = describeDesktopProxy(config, TAKEN_OVER)

  assert.match(line, /http:\/\/127\.0\.0\.1:7895/)
  assert.match(line, /3 条/)
  assert.match(line, /geosite:streaming|说不清/, line)

  const seen: unknown[] = []
  const lanes = [{ setProxy: (value: unknown) => void seen.push(value) }, { setProxy: (value: unknown) => void seen.push(value) }]
  const result = await applyDesktopProxy(lanes, TAKEN_OVER)

  assert.equal(result.applied, 2)
  assert.deepEqual(result.failed, [])
  assert.equal(seen.length, 2)
  assert.deepEqual(seen[0], seen[1], '每一档拿到的必须是同一个答案，不是一档一个说法')
})

test('a half-read answer falls back to the system rather than to "direct" — and a broken lane never aborts the rest', async () => {
  let calls = 0
  const lanes = [
    {
      setProxy: () => {
        throw new Error('session is destroyed')
      }
    },
    { setProxy: () => void calls++ }
  ]
  const result = await applyDesktopProxy(lanes, TAKEN_OVER)

  assert.equal(result.applied, 1, '一档坏了不许带走其余几档')
  assert.equal(result.failed.length, 1)
  assert.match(result.failed[0], /session is destroyed/)
  assert.equal(calls, 1, '坏掉的那档之后的档仍然要拿到配置')

  for (const view of [null, undefined, {}, { declared: false, mode: 'system' }, { declared: true, mode: 'off' }, { ...TAKEN_OVER, proxy: '' }]) {
    assert.equal(desktopProxyConfig(view).mode, 'system', `读不出/没接管/没端口都不能变成"直连"：${JSON.stringify(view)}`)
  }
})
