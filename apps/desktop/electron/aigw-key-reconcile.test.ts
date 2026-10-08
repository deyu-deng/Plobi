/**
 * Tests for electron/aigw-key-reconcile.ts.
 *
 * Run with: node --test electron/aigw-key-reconcile.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * These钉 are relationships, not value lists: whatever the shell resolves must
 * either match what the gateway declares, or come back as a mismatch. A test
 * that only pinned `sk-local-dev-key` would keep passing after the two sides
 * drifted onto a *different* shared value -- which is the failure being guarded.
 *
 * Also pinned: the reconciliation output never carries key material. That is
 * not decoration -- the summary goes into the log ring, and the log ring ships
 * inside support bundles.
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  declaredGatewayApiKey,
  fingerprintKey,
  reconcileGatewayKey,
  usedGatewayKey,
} from './aigw-key-reconcile'

const DECLARED = 'sk-local-dev-key'
const OTHER = 'a-value-that-is-not-the-declared-one'

const configWith = (key: string) =>
  `server:\n  host: 127.0.0.1\n  port: 8000\n  api_key: ${key}\n\nlogging:\n  level: INFO\n`

test('declaredGatewayApiKey reads server.api_key and not a same-named key elsewhere', () => {
  const text = `${configWith(DECLARED)}other:\n  api_key: should-be-ignored\n`
  assert.equal(declaredGatewayApiKey(text, {}), DECLARED)
})

test('declaredGatewayApiKey expands ${VAR:-default} through the environment', () => {
  const text = configWith('${AIGW_KEY:-fallback-from-config}')
  assert.equal(declaredGatewayApiKey(text, { AIGW_KEY: 'from-env' }), 'from-env')
  assert.equal(declaredGatewayApiKey(text, {}), 'fallback-from-config')
})

test('declaredGatewayApiKey strips trailing comments and quotes like the Python reader', () => {
  assert.equal(declaredGatewayApiKey(configWith(`${DECLARED}  # rotated 2026-10-08`), {}), DECLARED)
  assert.equal(declaredGatewayApiKey(configWith(`"${DECLARED}"`), {}), DECLARED)
})

test('declaredGatewayApiKey returns null for missing file, block or key', () => {
  assert.equal(declaredGatewayApiKey(null, {}), null)
  assert.equal(declaredGatewayApiKey('logging:\n  level: INFO\n', {}), null)
  assert.equal(declaredGatewayApiKey('server:\n  port: 8000\n', {}), null)
})

test('reconcile passes when the shell fallback equals what the gateway declares', () => {
  const r = reconcileGatewayKey(configWith(DECLARED), {})
  assert.equal(r.match, true)
  assert.equal(r.usedSource, 'shell_default')
  assert.equal(r.declaredFp, fingerprintKey(DECLARED))
})

test('reconcile goes red when the environment carries a different key', () => {
  const r = reconcileGatewayKey(configWith(DECLARED), { AIGW_API_KEY: OTHER })
  assert.equal(r.match, false)
  assert.equal(r.usedSource, 'env:AIGW_API_KEY')
  assert.match(r.summary, /MISMATCH/)
})

test('an unreadable declaration is reported as NOT CHECKED, never as a pass', () => {
  const r = reconcileGatewayKey(null, {})
  assert.equal(r.match, false)
  assert.equal(r.declaredFp, null)
  assert.match(r.summary, /NOT CHECKED/)
})

test('no field returned by reconcile carries key material', () => {
  const r = reconcileGatewayKey(configWith(DECLARED), { AIGW_API_KEY: OTHER })
  const dump = JSON.stringify(r)
  assert.ok(!dump.includes(DECLARED), 'summary leaked the declared key')
  assert.ok(!dump.includes(OTHER), 'summary leaked the shell key')
  assert.ok(dump.includes(fingerprintKey(OTHER)), 'fingerprints are the point of the report')
})

test('the shell ladder keeps AIGW_API_KEY ahead of PLOBI_AIGW_API_KEY', () => {
  assert.equal(usedGatewayKey({ AIGW_API_KEY: 'first', PLOBI_AIGW_API_KEY: 'second' }).source, 'env:AIGW_API_KEY')
  assert.equal(usedGatewayKey({ PLOBI_AIGW_API_KEY: 'second' }).source, 'env:PLOBI_AIGW_API_KEY')
})
