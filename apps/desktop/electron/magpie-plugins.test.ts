/**
 * The packaged plugin set is a **floor**, never a ceiling.
 *
 * What must hold, and why each one is a test rather than a comment:
 *
 *   - a machine with no plugin set gets one (a fresh install has no providers
 *     at all otherwise — measured 2026-11-10: the host publishes an empty
 *     roster, so every subscription card reads "not installed");
 *   - a machine that already has a plugin set is **never** touched: the set is
 *     seeded, not imposed;
 *   - a magpie that was updated on its own keeps the plugins it manages — the
 *     packaged set is older by definition there, and pushing it back would be
 *     the one thing that makes an independently updated magpie misbehave;
 *   - the roster that ships carries **no sign-in state** (somebody's
 *     `signedIn` has no business inside an installer);
 *   - the plugin list pins the versions (a package that installs whatever the
 *     registry says today is not the package the build was verified against).
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  magpiePluginManifest,
  readPackagedPluginSet,
  seedMagpiePlugins
} from './magpie-plugins'

/** A packaged set with two plugins, one of them disabled. */
function makePackagedSet(root: string, options: { roster?: unknown[] } = {}) {
  const dir = path.join(root, 'packaged')
  const dist = path.join(dir, 'plugins', 'dist')
  fs.mkdirSync(dist, { recursive: true })

  const plugins = [
    { spec: '@magpie-community/opencode-workbuddy-auth', version: '0.2.26', sha256: '', source: 'npm' as const, tarball: 'workbuddy.tgz' },
    { spec: 'codex', version: '0.2.3', sha256: '', source: 'npm' as const, tarball: 'codex.tgz', off: true }
  ]

  for (const entry of plugins) {
    const bytes = Buffer.from(`tarball:${entry.spec}`)
    entry.sha256 = createHash('sha256').update(bytes).digest('hex')
    fs.writeFileSync(path.join(dist, entry.tarball), bytes)
  }

  fs.writeFileSync(
    path.join(dir, 'manifest.json'),
    JSON.stringify({ name: 'magpie-plugins', set_version: '2026.11.10-1', magpie: 'v0.1.3403', plugins }, null, 1),
    'utf8'
  )
  fs.writeFileSync(
    path.join(dir, 'version.json'),
    JSON.stringify({ set_version: '2026.11.10-1', magpie: 'v0.1.3403', plugins: plugins.length }, null, 1),
    'utf8'
  )
  fs.writeFileSync(
    path.join(dir, 'plugin-providers.json'),
    JSON.stringify(options.roster ?? [{ id: 'workbuddy', name: 'WorkBuddy', methods: [{ type: 'oauth', label: 'browser' }] }], null, 1),
    'utf8'
  )

  return dir
}

function makeHome(root: string) {
  const home = path.join(root, 'home')
  fs.mkdirSync(path.join(home, 'magpie'), { recursive: true })

  return home
}

test('a machine with no plugin set gets the packaged one', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    const home = makeHome(root)
    const packaged = makePackagedSet(root)

    const result = seedMagpiePlugins(home, { packagedDir: packaged, runningMagpieVersion: 'v0.1.3403' })
    assert.equal(result.seeded, true, result.reason)

    const listPath = path.join(home, 'magpie', 'plugins.json')
    assert.ok(fs.existsSync(listPath), 'the plugin list was not written')

    const list = JSON.parse(fs.readFileSync(listPath, 'utf8'))
    assert.equal(list.plugins.length, 2)
    assert.equal(list.plugins[0].spec, '@magpie-community/opencode-workbuddy-auth@0.2.26')
    assert.equal(list.plugins[1].off, true)

    // The tarballs land where magpie looks for them.
    assert.ok(fs.existsSync(path.join(home, 'magpie', 'plugins', 'dist', 'workbuddy.tgz')))
    assert.ok(fs.existsSync(path.join(home, 'magpie', 'plugin-providers.json')))

    const marker = JSON.parse(fs.readFileSync(path.join(home, 'magpie', 'plugins-set.json'), 'utf8'))
    assert.equal(marker.set_version, '2026.11.10-1')
    assert.equal(marker.seeded_by, 'packaged')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a machine that already has a plugin set is never touched', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    const home = makeHome(root)
    const packaged = makePackagedSet(root)
    const listPath = path.join(home, 'magpie', 'plugins.json')
    fs.writeFileSync(listPath, '{"plugins":[{"spec":"@magpie-community/opencode-grok-auth@0.2.6"}]}', 'utf8')

    const result = seedMagpiePlugins(home, { packagedDir: packaged, runningMagpieVersion: 'v0.1.3403' })
    assert.equal(result.seeded, false)
    assert.match(result.reason, /already has a plugin set/)

    assert.equal(fs.readFileSync(listPath, 'utf8'), '{"plugins":[{"spec":"@magpie-community/opencode-grok-auth@0.2.6"}]}')
    assert.ok(!fs.existsSync(path.join(home, 'magpie', 'plugins-set.json')), 'it left a marker behind')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a magpie that was updated on its own keeps the plugins it manages', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    const home = makeHome(root)
    const packaged = makePackagedSet(root)

    const result = seedMagpiePlugins(home, { packagedDir: packaged, runningMagpieVersion: 'v0.1.3500' })
    assert.equal(result.seeded, false)
    assert.match(result.reason, /newer than the packaged set/)

    assert.ok(!fs.existsSync(path.join(home, 'magpie', 'plugins.json')))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('a build that carries no plugin set is a no-op, not a failure', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    const home = makeHome(root)
    const result = seedMagpiePlugins(home, { packagedDir: path.join(root, 'nothing-here') })
    assert.equal(result.seeded, false)
    assert.match(result.reason, /carries no plugin set/)
    assert.ok(!fs.existsSync(path.join(home, 'magpie', 'plugins.json')))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('the packaged roster carries no sign-in state', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    const home = makeHome(root)

    const packaged = makePackagedSet(root, {
      roster: [{ id: 'workbuddy', name: 'WorkBuddy', signedIn: true, accountId: 'acct-1', accounts: [{ id: 'a' }], authType: 'oauth' }]
    })

    seedMagpiePlugins(home, { packagedDir: packaged, runningMagpieVersion: 'v0.1.3403' })

    const roster = JSON.parse(fs.readFileSync(path.join(home, 'magpie', 'plugin-providers.json'), 'utf8'))
    assert.equal(roster[0].signedIn, undefined)
    assert.equal(roster[0].accountId, undefined)
    assert.equal(roster[0].accounts, undefined)
    assert.equal(roster[0].authType, undefined)
    assert.equal(roster[0].name, 'WorkBuddy')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('the plugin list pins the version the build was verified against', () => {
  const manifest = magpiePluginManifest([
    { spec: '@magpie-community/opencode-workbuddy-auth', version: '0.2.26', sha256: 'x', source: 'npm', tarball: 'a.tgz' },
    { spec: 'codex', version: '0.2.3', sha256: 'x', source: 'npm', tarball: 'b.tgz', off: true }
  ])

  const parsed = JSON.parse(manifest)
  assert.equal(parsed.plugins[0].spec, '@magpie-community/opencode-workbuddy-auth@0.2.26')
  assert.ok(!manifest.includes('@latest'), 'the list must not ask for whatever is newest today')
  assert.equal(parsed.plugins[1].off, true)
})

test('a packaged set whose files are missing reads as "no set"', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-plugins-'))

  try {
    assert.equal(readPackagedPluginSet(path.join(root, 'nothing')), null)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
