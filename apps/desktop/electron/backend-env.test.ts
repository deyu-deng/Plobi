import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import {
  appendUniquePathEntries,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  desktopCliSearchPath,
  normalizePlobiHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES
} from './backend-env'

test('desktop backend PATH adds Plobi-managed bins and missing POSIX sane entries', () => {
  const result = buildDesktopBackendPath({
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: '/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries[0], '/Users/test/.plobi/node/bin')
  assert.equal(entries[1], '/Users/test/.plobi/plobi-agent/venv/bin')
  assert.ok(entries.includes('/opt/homebrew/bin'), 'Apple Silicon Homebrew bin is added')
  assert.ok(entries.includes('/opt/homebrew/sbin'), 'Apple Silicon Homebrew sbin is added')
  assert.ok(entries.includes('/usr/local/sbin'), 'missing standard sbin is added')

  for (const expected of POSIX_SANE_PATH_ENTRIES) {
    assert.ok(entries.includes(expected), `${expected} should be present`)
  }
})

test('desktop backend PATH preserves first occurrence and avoids duplicates', () => {
  const result = buildDesktopBackendPath({
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: '/opt/homebrew/bin:/usr/bin:/opt/homebrew/bin:/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries.filter(entry => entry === '/opt/homebrew/bin').length, 1)
  assert.ok(
    entries.indexOf('/opt/homebrew/bin') < entries.indexOf('/opt/homebrew/sbin'),
    'existing Homebrew bin keeps its precedence over appended missing sane entries'
  )
})

test('buildDesktopBackendEnv extends PYTHONPATH and backend PATH together', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: '/Users/test/.plobi',
    pythonPathEntries: ['/repo/plobi-agent'],
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentEnv: {
      PATH: '/usr/bin:/bin',
      PYTHONPATH: '/existing/pythonpath'
    },
    platform: 'darwin',
    pathModule: path.posix
  })

  assert.equal(env.PYTHONPATH, '/repo/plobi-agent:/existing/pythonpath')
  assert.ok(env.PATH.startsWith('/Users/test/.plobi/node/bin:/Users/test/.plobi/plobi-agent/venv/bin:'))
  assert.ok(env.PATH.includes('/opt/homebrew/bin'))
})

test('normalizePlobiHomeRoot maps profile homes back to the global Plobi root', () => {
  assert.equal(
    normalizePlobiHomeRoot('/Users/test/.plobi/profiles/oracle', { pathModule: path.posix }),
    '/Users/test/.plobi'
  )
  assert.equal(
    normalizePlobiHomeRoot('C:\\Users\\test\\AppData\\Local\\plobi\\profiles\\oracle', { pathModule: path.win32 }),
    'C:\\Users\\test\\AppData\\Local\\plobi'
  )
  assert.equal(normalizePlobiHomeRoot('/Users/test/.plobi', { pathModule: path.posix }), '/Users/test/.plobi')
})

test('Windows PATH casing and delimiter are preserved without POSIX sane entries', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: 'C:\\Users\\test\\AppData\\Local\\plobi',
    pythonPathEntries: ['C:\\repo\\plobi-agent'],
    venvRoot: 'C:\\Users\\test\\AppData\\Local\\plobi\\plobi-agent\\venv',
    currentEnv: {
      Path: 'C:\\Windows\\System32;C:\\Windows',
      PYTHONPATH: 'C:\\existing\\pythonpath'
    },
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(pathEnvKey({ Path: 'x' }, 'win32'), 'Path')
  assert.equal(env.PATH, undefined)
  assert.ok(env.Path.startsWith('C:\\Users\\test\\AppData\\Local\\plobi\\node\\bin;'))
  assert.ok(env.Path.includes('\\venv\\Scripts;'))
  assert.ok(env.Path.includes(';C:\\Windows\\System32;C:\\Windows'))
  assert.equal(env.Path.includes('/opt/homebrew/bin'), false)
})

test('appendUniquePathEntries drops empty entries and keeps first occurrence', () => {
  assert.equal(appendUniquePathEntries([':/a::/b', ['/a', '/c']], { delimiter: ':' }), '/a:/b:/c')
})

test('desktopCliSearchPath finds a plobi under ~/.local/bin when the GUI PATH omits it', () => {
  // The exact Finder/Dock case: launchd hands the app /usr/bin:/bin:/usr/sbin:/sbin,
  // which never includes ~/.local/bin — where the user's working `plobi` symlink
  // lives. The injected per-user dirs make an already-installed CLI reachable
  // without a shell. Nothing here touches the real filesystem or PATH.
  const guiPath = '/usr/bin:/bin:/usr/sbin:/sbin'
  const search = desktopCliSearchPath({
    home: '/Users/test',
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: guiPath,
    platform: 'darwin',
    pathModule: path.posix
  })

  const dirs = search.split(':')
  assert.ok(dirs.includes('/Users/test/.local/bin'), 'per-user ~/.local/bin is added')
  assert.ok(dirs.includes('/Users/test/.cargo/bin'), 'per-user ~/.cargo/bin is added')
  assert.ok(dirs.includes('/opt/homebrew/bin'), 'the backend sane-PATH dirs are reused')

  // findOnPath-equivalent probe over that PATH, with a plobi present only in the
  // injected ~/.local/bin, must locate it.
  const present = new Set(['/Users/test/.local/bin/plobi'])
  const found = dirs.map(dir => path.posix.join(dir, 'plobi')).find(candidate => present.has(candidate))
  assert.equal(found, '/Users/test/.local/bin/plobi', 'a CLI in ~/.local/bin is discovered')
})

test('desktopCliSearchPath leaves the Windows search path unchanged', () => {
  // Windows CLI discovery is installer/registry-driven; a bare dir scan there can
  // resolve the wrong extensionless file, so no per-user dirs are prepended.
  const search = desktopCliSearchPath({
    home: 'C:\\Users\\test',
    plobiHome: 'C:\\Users\\test\\.plobi',
    venvRoot: 'C:\\Users\\test\\.plobi\\plobi-agent\\venv',
    currentPath: 'C:\\Windows\\System32',
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(search.includes('.local'), false)
  assert.equal(search.includes('.cargo'), false)
})

