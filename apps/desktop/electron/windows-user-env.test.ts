import assert from 'node:assert/strict'
import { test } from 'node:test'

import { expandWindowsEnvRefs, parseRegQueryValue, readWindowsUserEnvVar } from './windows-user-env'

// ── parseRegQueryValue ─────────────────────────────────────────────────────

test('parseRegQueryValue extracts a REG_SZ value', () => {
  const out = ['', 'HKEY_CURRENT_USER\\Environment', '    PLOBI_HOME    REG_SZ    F:\\Plobi\\data', ''].join('\r\n')
  assert.equal(parseRegQueryValue(out, 'PLOBI_HOME'), 'F:\\Plobi\\data')
})

test('parseRegQueryValue matches the name case-insensitively', () => {
  const out = 'HKEY_CURRENT_USER\\Environment\r\n    Plobi_Home    REG_EXPAND_SZ    %USERPROFILE%\\h\r\n'
  assert.equal(parseRegQueryValue(out, 'PLOBI_HOME'), '%USERPROFILE%\\h')
})

test('parseRegQueryValue preserves spaces inside the value', () => {
  const out = '    PLOBI_HOME    REG_SZ    C:\\Program Files\\Plobi\r\n'
  assert.equal(parseRegQueryValue(out, 'PLOBI_HOME'), 'C:\\Program Files\\Plobi')
})

test('parseRegQueryValue returns null when the value line is absent', () => {
  const out = 'HKEY_CURRENT_USER\\Environment\r\n    Path    REG_SZ    C:\\x\r\n'
  assert.equal(parseRegQueryValue(out, 'PLOBI_HOME'), null)
  assert.equal(parseRegQueryValue('', 'PLOBI_HOME'), null)
  assert.equal(parseRegQueryValue('garbage', 'PLOBI_HOME'), null)
})

// ── expandWindowsEnvRefs ───────────────────────────────────────────────────

test('expandWindowsEnvRefs expands %VAR% case-insensitively', () => {
  assert.equal(expandWindowsEnvRefs('%UserProfile%\\h', { USERPROFILE: 'C:\\Users\\jeff' }), 'C:\\Users\\jeff\\h')
})

test('expandWindowsEnvRefs leaves literal paths and unknown refs intact', () => {
  assert.equal(expandWindowsEnvRefs('F:\\Plobi\\data', {}), 'F:\\Plobi\\data')
  assert.equal(expandWindowsEnvRefs('%NOPE%\\x', {}), '%NOPE%\\x')
})

// ── readWindowsUserEnvVar ──────────────────────────────────────────────────

test('readWindowsUserEnvVar returns null off Windows without spawning', () => {
  let spawned = false

  const exec = () => {
    spawned = true

    return ''
  }

  assert.equal(readWindowsUserEnvVar('PLOBI_HOME', { platform: 'linux', exec }), null)
  assert.equal(spawned, false)
})

test('readWindowsUserEnvVar queries HKCU\\Environment and expands the value', () => {
  const calls = []

  const exec = (cmd, args) => {
    calls.push([cmd, args])

    return 'HKEY_CURRENT_USER\\Environment\r\n    PLOBI_HOME    REG_EXPAND_SZ    %DRIVE%\\Plobi\r\n'
  }

  const value = readWindowsUserEnvVar('PLOBI_HOME', {
    platform: 'win32',
    env: { DRIVE: 'F:' },
    exec
  })

  assert.equal(value, 'F:\\Plobi')
  assert.deepEqual(calls, [['reg', ['query', 'HKCU\\Environment', '/v', 'PLOBI_HOME']]])
})

test('readWindowsUserEnvVar returns null when reg exits non-zero (value missing)', () => {
  const exec = () => {
    throw new Error('reg exited 1')
  }

  assert.equal(readWindowsUserEnvVar('PLOBI_HOME', { platform: 'win32', exec }), null)
})

test('readWindowsUserEnvVar returns null for an empty value', () => {
  const exec = () => '    PLOBI_HOME    REG_SZ    \r\n'
  assert.equal(readWindowsUserEnvVar('PLOBI_HOME', { platform: 'win32', exec }), null)
})
