/**
 * The plugin set that ships has to be exactly what the pin says.
 *
 * What must hold, and why each one is a test rather than a comment:
 *
 *   - a tarball whose hash does not match the pin is a hard stop (the plugins
 *     carry the vendor adapters — the whole subscription layer);
 *   - a checked-in tarball is not a trusted one: it is hash-checked too;
 *   - the roster we ship carries no per-machine sign-in state (somebody's
 *     `signedIn` has no business inside an installer);
 *   - the manifest and the marker are written even when a fetch failed, so the
 *     build can say *what* is missing instead of shipping a half set.
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  fetchPluginSet,
  readPluginPin,
  sanitizeRoster
} from './fetch-magpie-plugins.mjs'

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

/** A repo tree with one vendored plugin and a pin that names it. */
function makeRepo(root, { tamper = false } = {}) {
  const vendor = path.join(root, 'third_party', 'magpie-plugins', 'tarballs')
  fs.mkdirSync(vendor, { recursive: true })

  const bytes = Buffer.from('tarball:@magpie-community/opencode-copilot-auth')
  fs.writeFileSync(path.join(vendor, 'copilot.tgz'), bytes)

  const roster = [{ id: 'github-copilot', name: 'GitHub Copilot', signedIn: true, accountId: 'acct-1', methods: [] }]
  fs.writeFileSync(path.join(root, 'third_party', 'magpie-plugins', 'roster.json'), JSON.stringify(roster, null, 1), 'utf8')

  const pin = {
    name: 'magpie-plugins',
    set_version: '2026.11.10-1',
    magpie: 'v0.1.3403',
    plugins: [
      {
        spec: '@magpie-community/opencode-copilot-auth',
        version: '0.1.2',
        sha256: sha256(bytes),
        source: 'vendored',
        tarball: 'copilot.tgz'
      }
    ],
    roster: { file: 'roster.json', sha256: sha256(Buffer.from(JSON.stringify(roster, null, 1), 'utf8')) }
  }
  if (tamper) pin.plugins[0].sha256 = 'f'.repeat(64)

  fs.mkdirSync(path.join(root, 'third_party'), { recursive: true })
  fs.writeFileSync(path.join(root, 'third_party', 'magpie-plugins.json'), JSON.stringify(pin, null, 1), 'utf8')
  return root
}

test('the pin is read from the one place it lives', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-plugin-pin-'))
  try {
    makeRepo(root)
    const pin = readPluginPin(root)
    assert.equal(pin.set_version, '2026.11.10-1')
    assert.equal(pin.plugins.length, 1)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('the roster we ship carries no sign-in state', () => {
  const rows = sanitizeRoster([
    { id: 'workbuddy', name: 'WorkBuddy', signedIn: true, authType: 'oauth', accountId: 'a', accounts: [{ id: 'x' }], methods: [{ type: 'oauth' }] }
  ])

  assert.equal(rows[0].signedIn, undefined)
  assert.equal(rows[0].authType, undefined)
  assert.equal(rows[0].accountId, undefined)
  assert.equal(rows[0].accounts, undefined)
  assert.equal(rows[0].name, 'WorkBuddy')
  assert.equal(rows[0].methods.length, 1)
})

test('a vendored tarball is copied in and hashed', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-plugin-fetch-'))
  try {
    makeRepo(root)
    const outDir = path.join(root, 'build', 'magpie-plugins')

    const result = await fetchPluginSet({ repoRootDir: root, outDir })
    assert.deepEqual(result.problems, [])

    assert.ok(fs.existsSync(path.join(outDir, 'plugins', 'dist', 'copilot.tgz')))

    const manifest = JSON.parse(fs.readFileSync(path.join(outDir, 'manifest.json'), 'utf8'))
    assert.equal(manifest.plugins.length, 1)

    const marker = JSON.parse(fs.readFileSync(path.join(outDir, 'version.json'), 'utf8'))
    assert.equal(marker.set_version, '2026.11.10-1')
    assert.equal(marker.magpie, 'v0.1.3403')
    assert.equal(marker.plugins, 1)

    // The roster that ships is the sanitized one.
    const roster = JSON.parse(fs.readFileSync(path.join(outDir, 'plugin-providers.json'), 'utf8'))
    assert.equal(roster[0].signedIn, undefined)
    assert.equal(roster[0].accountId, undefined)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a tarball that does not match the pin is a hard stop', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-plugin-fetch-'))
  try {
    makeRepo(root, { tamper: true })
    const outDir = path.join(root, 'build', 'magpie-plugins')

    const result = await fetchPluginSet({ repoRootDir: root, outDir })
    assert.equal(result.problems.length, 1)
    assert.match(result.problems[0], /does not match the pin/)
    assert.ok(!fs.existsSync(path.join(outDir, 'plugins', 'dist', 'copilot.tgz')), 'it shipped the wrong bytes')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a roster that does not match the pin is a hard stop', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-plugin-fetch-'))
  try {
    makeRepo(root)
    const rosterPath = path.join(root, 'third_party', 'magpie-plugins', 'roster.json')
    fs.writeFileSync(rosterPath, '[{"id":"github-copilot"}]', 'utf8')

    const result = await fetchPluginSet({ repoRootDir: root, outDir: path.join(root, 'build', 'magpie-plugins') })
    assert.equal(result.problems.length, 1)
    assert.match(result.problems[0], /roster does not match/)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
