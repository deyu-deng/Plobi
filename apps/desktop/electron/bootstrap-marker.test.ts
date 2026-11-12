import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BOOTSTRAP_MARKER_SCHEMA_VERSION,
  evaluateBootstrapMarker,
  MIN_PINNED_COMMIT_LENGTH
} from './bootstrap-marker'

const SHA = '345c35b4ed9cecd2f59d667fcaec379d7d215d53'

function marker(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: BOOTSTRAP_MARKER_SCHEMA_VERSION,
    pinnedCommit: SHA,
    pinnedBranch: 'v0.3.1',
    completedAt: '2026-11-11T10:55:36.580Z',
    desktopVersion: '0.3.1',
    ...overrides
  }
}

test('missing marker is not complete', () => {
  const verdict = evaluateBootstrapMarker(null, true)

  assert.equal(verdict.complete, false)
  assert.equal(verdict.needsRepair, false)
  assert.match(verdict.reason, /missing/)
})

test('non-object marker is not complete', () => {
  for (const value of ['done', 1, true]) {
    const verdict = evaluateBootstrapMarker(value, true)

    assert.equal(verdict.complete, false, `value ${String(value)}`)
    assert.equal(verdict.needsRepair, false)
  }
})

test('schema mismatch is not complete and never repaired', () => {
  const verdict = evaluateBootstrapMarker(marker({ schemaVersion: 2 }), true)

  assert.equal(verdict.complete, false)
  assert.equal(verdict.needsRepair, false)
  assert.match(verdict.reason, /schemaVersion/)
})

test('valid marker over a usable runtime is complete', () => {
  const verdict = evaluateBootstrapMarker(marker(), true)

  assert.equal(verdict.complete, true)
  assert.equal(verdict.pinnedCommitValid, true)
  assert.equal(verdict.needsRepair, false)
})

test('valid marker over an unusable runtime is not complete', () => {
  const verdict = evaluateBootstrapMarker(marker(), false)

  assert.equal(verdict.complete, false)
  assert.equal(verdict.needsRepair, false)
  assert.match(verdict.reason, /not usable/)
})

test('the synthetic bundled attestation is accepted', () => {
  const verdict = evaluateBootstrapMarker(marker({ pinnedCommit: 'bundled' }), true)

  assert.equal('bundled'.length >= MIN_PINNED_COMMIT_LENGTH, true)
  assert.equal(verdict.complete, true)
  assert.equal(verdict.pinnedCommitValid, true)
  assert.equal(verdict.needsRepair, false)
})

test('legacy null pinnedCommit is repaired instead of re-running bootstrap', () => {
  const verdict = evaluateBootstrapMarker(marker({ pinnedCommit: null }), true)

  assert.equal(verdict.complete, true, 'runtime is usable, so the install counts as done')
  assert.equal(verdict.pinnedCommitValid, false)
  assert.equal(verdict.needsRepair, true)
  assert.match(verdict.reason, /pinnedCommit was null/)
})

test('legacy empty / truncated / non-string pinnedCommit is repaired too', () => {
  for (const broken of ['', 'abc', 0, false, undefined]) {
    const verdict = evaluateBootstrapMarker(marker({ pinnedCommit: broken }), true)

    assert.equal(verdict.complete, true, `value ${String(broken)}`)
    assert.equal(verdict.needsRepair, true, `value ${String(broken)}`)
  }
})

test('broken pinnedCommit over an unusable runtime is NOT repaired', () => {
  const verdict = evaluateBootstrapMarker(marker({ pinnedCommit: null }), false)

  assert.equal(verdict.complete, false, 'must fall through to a real install')
  assert.equal(verdict.needsRepair, false, 'rewriting the marker here would hide a broken install')
})
