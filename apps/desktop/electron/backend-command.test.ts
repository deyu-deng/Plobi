'use strict'

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  dashboardFallbackArgs,
  readEnvValue,
  resolveLanMode,
  serveBackendArgs,
  sourceDeclaresServe,
} from './backend-command'

test('serveBackendArgs builds a headless serve invocation', () => {
  assert.deepEqual(
    serveBackendArgs(undefined, { env: {} }),
    ['serve', '--host', '127.0.0.1', '--port', '0'],
  )
})

test('serveBackendArgs pins a profile when provided', () => {
  assert.deepEqual(
    serveBackendArgs('worker', { env: {} }),
    ['--profile', 'worker', 'serve', '--host', '127.0.0.1', '--port', '0'],
  )
})

test('serveBackendArgs binds LAN (0.0.0.0:8787) when PLOBI_LAN=1 is set', () => {
  assert.deepEqual(
    serveBackendArgs(undefined, { env: { PLOBI_LAN: '1' } }),
    ['serve', '--host', '0.0.0.0', '--port', '8787'],
  )
})

test('serveBackendArgs LAN-mode also pins the profile when provided', () => {
  assert.deepEqual(
    serveBackendArgs('worker', { env: { PLOBI_LAN: '1' } }),
    ['--profile', 'worker', 'serve', '--host', '0.0.0.0', '--port', '8787'],
  )
})

test('serveBackendArgs falls back to loopback when PLOBI_LAN is anything other than "1"', () => {
  for (const value of ['', '0', 'true', 'yes', 'on', 'NO', '  ']) {
    assert.deepEqual(
      serveBackendArgs(undefined, { env: { PLOBI_LAN: value } }),
      ['serve', '--host', '127.0.0.1', '--port', '0'],
      `value=${JSON.stringify(value)}`,
    )
  }
})

test('serveBackendArgs honours PLOBI_LAN=1 from $PLOBI_HOME/.env when env is unset', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-backend-cmd-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(envPath, 'PLOBI_LAN=1\n', 'utf8')
    assert.deepEqual(
      serveBackendArgs(undefined, { env: {}, plobiHomeEnvPath: envPath }),
      ['serve', '--host', '0.0.0.0', '--port', '8787'],
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('serveBackendArgs reads PLOBI_LAN=1 even when commented-style noise surrounds it', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-backend-cmd-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(
      envPath,
      [
        '# leading comment',
        'OTHER=value',
        'PLOBI_LAN="1"   # inline comment',
        'TAIL=last',
      ].join('\n'),
      'utf8',
    )
    assert.deepEqual(
      serveBackendArgs(undefined, { env: {}, plobiHomeEnvPath: envPath }),
      ['serve', '--host', '0.0.0.0', '--port', '8787'],
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('serveBackendArgs ignores malformed .env lines and stays loopback', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-backend-cmd-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(envPath, 'this is not a kv line\n', 'utf8')
    assert.deepEqual(
      serveBackendArgs(undefined, { env: {}, plobiHomeEnvPath: envPath }),
      ['serve', '--host', '127.0.0.1', '--port', '0'],
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('serveBackendArgs inherits process.env.PLOBI_LAN when explicitly overridden (no .env fallback)', () => {
  // Explicit non-`1` value wins — never second-guess the shell.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-backend-cmd-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(envPath, 'PLOBI_LAN=1\n', 'utf8')
    assert.deepEqual(
      serveBackendArgs(undefined, { env: { PLOBI_LAN: '0' }, plobiHomeEnvPath: envPath }),
      ['serve', '--host', '127.0.0.1', '--port', '0'],
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('dashboardFallbackArgs rewrites serve -> dashboard --no-open, keeping the -m prefix', () => {
  const serve = ['-m', 'plobi_cli.main', 'serve', '--host', '127.0.0.1', '--port', '0']
  assert.deepEqual(dashboardFallbackArgs(serve), [
    '-m',
    'plobi_cli.main',
    'dashboard',
    '--no-open',
    '--host',
    '127.0.0.1',
    '--port',
    '0'
  ])
})

test('dashboardFallbackArgs preserves a --profile flag ahead of serve', () => {
  const serve = ['-m', 'plobi_cli.main', '--profile', 'worker', 'serve', '--host', '127.0.0.1', '--port', '0']
  assert.deepEqual(dashboardFallbackArgs(serve), [
    '-m',
    'plobi_cli.main',
    '--profile',
    'worker',
    'dashboard',
    '--no-open',
    '--host',
    '127.0.0.1',
    '--port',
    '0'
  ])
})

test('dashboardFallbackArgs is a no-op (copy) when there is no serve token', () => {
  const args = ['-m', 'plobi_cli.main', 'dashboard', '--no-open']
  const out = dashboardFallbackArgs(args)
  assert.deepEqual(out, args)
  assert.notEqual(out, args, 'should return a copy, not the same reference')
})

test('sourceDeclaresServe detects the serve subparser registration', () => {
  assert.equal(sourceDeclaresServe('subparsers.add_parser("serve", help="...")'), true)
  assert.equal(sourceDeclaresServe("subparsers.add_parser('serve')"), true)
  assert.equal(sourceDeclaresServe('subparsers.add_parser(\n        "serve",\n)'), true)
})

test('sourceDeclaresServe does not false-positive on the substring "server"', () => {
  const oldSource = `
    dashboard_parser = subparsers.add_parser("dashboard", help="Start the web UI dashboard")
    from plobi_cli.web_server import start_server  # web server
  `

  assert.equal(sourceDeclaresServe(oldSource), false)
})

test('readEnvValue returns null on missing file or missing key', () => {
  assert.equal(readEnvValue(undefined, 'PLOBI_LAN'), null)
  assert.equal(readEnvValue('/nonexistent/path/.env', 'PLOBI_LAN'), null)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-read-env-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(envPath, 'OTHER=1\n', 'utf8')
    assert.equal(readEnvValue(envPath, 'PLOBI_LAN'), null)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('resolveLanMode honours inherited env without .env fallback when explicitly falsey', () => {
  // A non-empty, non-truthy shell value must short-circuit the .env lookup.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-resolve-lan-'))
  try {
    const envPath = path.join(dir, '.env')
    fs.writeFileSync(envPath, 'PLOBI_LAN=1\n', 'utf8')
    assert.equal(resolveLanMode({ PLOBI_LAN: '0' }, envPath), false)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})
