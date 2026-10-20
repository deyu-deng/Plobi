import assert from 'node:assert/strict'
import test from 'node:test'

import { hasProxyEnv, parseChromiumProxyResult, proxyFromProxyRules, proxyUrlToGitEnv } from './git-proxy'

// FAIL-BEFORE: the desktop's update check spawned `git` with a bare
// `{...process.env}`, so on a machine where direct egress to github.com is
// intercepted the child failed with `Failed to connect to github.com:443` and
// the panel could only say "couldn't reach the update server". These pin the
// parsing that turns an egress answer into `http_proxy`-shaped env vars.

test('resolveProxy DIRECT means "leave git alone"', () => {
  assert.equal(parseChromiumProxyResult('DIRECT'), null)
  assert.equal(parseChromiumProxyResult(''), null)
  assert.equal(parseChromiumProxyResult(null), null)
})

test('resolveProxy PROXY host:port becomes an http proxy URL', () => {
  assert.equal(parseChromiumProxyResult('PROXY 127.0.0.1:7890'), 'http://127.0.0.1:7890')
})

test('resolveProxy takes Chromium\'s first entry when several are listed', () => {
  assert.equal(parseChromiumProxyResult('PROXY a.example:1; PROXY b.example:2'), 'http://a.example:1')
})

test('resolveProxy keeps the SOCKS flavour curl understands', () => {
  assert.equal(parseChromiumProxyResult('SOCKS5 127.0.0.1:1080'), 'socks5://127.0.0.1:1080')
  assert.equal(parseChromiumProxyResult('SOCKS 127.0.0.1:1080'), 'socks4://127.0.0.1:1080')
})

test('resolveProxy junk is treated as "no answer", not as a proxy', () => {
  assert.equal(parseChromiumProxyResult('QUIC 127.0.0.1:7890'), null)
  assert.equal(parseChromiumProxyResult('   '), null)
})

test('proxyRules: a bare host:port applies to every scheme', () => {
  assert.equal(proxyFromProxyRules('127.0.0.1:7890'), 'http://127.0.0.1:7890')
})

test('proxyRules: explicit http/https entries win in order', () => {
  assert.equal(proxyFromProxyRules('http=p.example:80;https=p.example:443'), 'http://p.example:80')
  // First entry wins, and it keeps its own scheme.
  assert.equal(proxyFromProxyRules('https=p.example:443;http=p.example:80'), 'https://p.example:443')
})

test('proxyRules: an explicit scheme is preserved', () => {
  assert.equal(proxyFromProxyRules('socks5://p.example:1080'), 'socks5://p.example:1080')
})

test('proxyRules: direct / bypass entries are not a proxy', () => {
  assert.equal(proxyFromProxyRules('direct://'), null)
  assert.equal(proxyFromProxyRules('*,direct://'), null)
  assert.equal(proxyFromProxyRules(''), null)
  assert.equal(proxyFromProxyRules(null), null)
  // A bypass list that only contains direct entries must not yield a host.
  assert.equal(proxyFromProxyRules('http=;https='), null)
})

test('proxy env: all three scheme vars are set, trailing slash dropped', () => {
  assert.deepEqual(proxyUrlToGitEnv('http://127.0.0.1:7890/'), {
    http_proxy: 'http://127.0.0.1:7890',
    HTTP_PROXY: 'http://127.0.0.1:7890',
    https_proxy: 'http://127.0.0.1:7890',
    HTTPS_PROXY: 'http://127.0.0.1:7890',
    all_proxy: 'http://127.0.0.1:7890',
    ALL_PROXY: 'http://127.0.0.1:7890'
  })
})

test('proxy env: an empty URL adds nothing', () => {
  assert.deepEqual(proxyUrlToGitEnv('   '), {})
})

test('a launcher-provided proxy is detected, in either case', () => {
  assert.equal(hasProxyEnv({ https_proxy: 'http://127.0.0.1:7890' }), true)
  assert.equal(hasProxyEnv({ HTTPS_PROXY: 'http://127.0.0.1:7890' }), true)
  assert.equal(hasProxyEnv({ https_proxy: '   ' }), false)
  assert.equal(hasProxyEnv({}), false)
  assert.equal(hasProxyEnv(undefined), false)
})
