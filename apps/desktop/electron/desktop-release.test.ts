/**
 * Tests for electron/desktop-release.ts (the one-click update path).
 *
 * Run with: node --import tsx --test electron/desktop-release.test.ts
 * (Wired into npm run test:desktop:platforms in package.json.)
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  canSelfUpdate,
  compareVersions,
  parseReleaseTag,
  pickInstallerAsset,
  writeUpdateHelper
} from './desktop-release'

function release(assets: Array<{ name: string; size?: number }>) {
  return {
    tag: 'v0.3.2',
    version: '0.3.2',
    htmlUrl: 'https://github.com/deyu-deng/Plobi/releases',
    assets: assets.map((a) => ({
      name: a.name,
      size: a.size ?? 1,
      browser_download_url: `https://example.invalid/${a.name}`
    }))
  }
}

test('compareVersions compares numerically, not lexicographically', () => {
  assert.equal(compareVersions('0.3.10', '0.3.9'), 1)
  assert.equal(compareVersions('0.3.2', '0.3.2'), 0)
  assert.equal(compareVersions('0.2.9', '0.3.0'), -1)
  assert.equal(compareVersions('1.0.0', '0.99.99'), 1)
  assert.equal(compareVersions('v0.3.2', '0.3.2'), 0)
})

test('parseReleaseTag accepts only three-part version tags', () => {
  assert.equal(parseReleaseTag('v0.3.2'), '0.3.2')
  assert.equal(parseReleaseTag('0.3.2'), '0.3.2')
  assert.equal(parseReleaseTag('v0.3'), null)
  assert.equal(parseReleaseTag('nightly'), null)
})

test('parseReleaseTag rejects date-shaped tags (a date beats every 0.x)', () => {
  // The CalVer releases were renamed to the product version they always
  // stood for on 2026-11-06, but the guard stays: numerically 2026.10.20 is
  // "newer" than 0.3.2, so the updater must never read a date-shaped tag as
  // a version.
  assert.equal(parseReleaseTag('v2026.10.20'), null)
  assert.equal(parseReleaseTag('v2026.10.19.1'), null)
})

test('pickInstallerAsset finds the Windows installer', () => {
  const r = release([
    { name: 'Plobi-0.3.2-mac-arm64.zip' },
    { name: 'Plobi-0.3.2-win-x64.exe' },
    { name: 'Plobi-0.3.2-linux-x86_64.AppImage' }
  ])

  const hit = pickInstallerAsset(r, 'win32', 'x64')

  assert.ok(hit)
  assert.equal(hit!.name, 'Plobi-0.3.2-win-x64.exe')
})

test('pickInstallerAsset takes the AppImage on linux, not the deb', () => {
  const r = release([
    { name: 'Plobi-0.3.2-linux-amd64.deb' },
    { name: 'Plobi-0.3.2-linux-x86_64.AppImage' }
  ])

  const hit = pickInstallerAsset(r, 'linux', 'x64')

  assert.ok(hit)
  assert.equal(hit!.name, 'Plobi-0.3.2-linux-x86_64.AppImage')
})

test('pickInstallerAsset takes the zip on macOS (a dmg would need mounting)', () => {
  const r = release([
    { name: 'Plobi-0.3.2-mac-arm64.dmg' },
    { name: 'Plobi-0.3.2-mac-arm64.zip' }
  ])

  const hit = pickInstallerAsset(r, 'darwin', 'arm64')

  assert.ok(hit)
  assert.equal(hit!.name, 'Plobi-0.3.2-mac-arm64.zip')
})

test('pickInstallerAsset returns null when this platform has no artifact', () => {
  const r = release([{ name: 'Plobi-0.3.2-win-x64.exe' }])

  assert.equal(pickInstallerAsset(r, 'linux', 'x64'), null)
  assert.equal(pickInstallerAsset(r, 'win32', 'arm64'), null)
})

test('canSelfUpdate says no on macOS (unsigned builds cannot be swapped in place)', () => {
  assert.equal(canSelfUpdate('win32'), true)
  assert.equal(canSelfUpdate('linux'), true)
  assert.equal(canSelfUpdate('darwin'), false)
})

test('the update helper moves the agent to the same tag, then the shell, then starts the app', () => {
  const helper = writeUpdateHelper({
    installerPath: 'C:\\Temp\\Plobi-0.3.2-win-x64.exe',
    installDir: 'D:\\Software\\Plobi',
    appPath: 'D:\\Software\\Plobi\\Plobi.exe',
    plobiHome: 'D:\\Data\\AppData\\plobi',
    tag: 'v0.3.2'
  })

  try {
    const text = fs.readFileSync(helper, 'utf8')

    // 1) the agent checkout goes to the release tag (shell and agent end up
    //    on ONE version number)
    assert.match(text, /-Tag "v0\.3\.2"/)
    assert.match(text, /install\.ps1/)
    // 2) the shell is replaced by the new installer
    assert.match(text, /Plobi-0\.3\.2-win-x64\.exe" \/S \/D=D:\\Software\\Plobi/)
    // 3) and the app starts again
    assert.match(text, /start "" "D:\\Software\\Plobi\\Plobi\.exe"/)
  } finally {
    try {
      fs.unlinkSync(helper)
    } catch {
      /* already gone */
    }
  }
})

test('the update helper waits for the app to exit before installing', () => {
  const helper = writeUpdateHelper({
    installerPath: 'C:\\Temp\\Plobi-0.3.2-win-x64.exe',
    installDir: 'D:\\Software\\Plobi',
    appPath: 'D:\\Software\\Plobi\\Plobi.exe',
    plobiHome: 'D:\\Data\\AppData\\plobi',
    tag: 'v0.3.2'
  })

  try {
    const text = fs.readFileSync(helper, 'utf8')

    // Installing over a running app half-succeeds: the wait has to come
    // FIRST, before the checkout and before the installer.
    assert.match(text, /tasklist \/FI "IMAGENAME eq Plobi\.exe"/)
    assert.match(text, /plobi_wait_exit/)

    const waitAt = text.indexOf('plobi_wait_exit')
    const installAt = text.indexOf('install.ps1')
    const shellAt = text.indexOf('/S /D=')

    assert.ok(waitAt >= 0 && installAt > waitAt, 'the wait must come before the checkout move')
    assert.ok(shellAt > installAt, 'the shell swap must come after the checkout move')
  } finally {
    try {
      fs.unlinkSync(helper)
    } catch {
      /* already gone */
    }
  }
})

test('the update helper leaves the checkout on a branch, not on a detached HEAD', () => {
  const helper = writeUpdateHelper({
    installerPath: 'C:\\Temp\\Plobi-0.3.2-win-x64.exe',
    installDir: 'D:\\Software\\Plobi',
    appPath: 'D:\\Software\\Plobi\\Plobi.exe',
    plobiHome: 'D:\\Data\\AppData\\plobi',
    tag: 'v0.3.2'
  })

  try {
    const text = fs.readFileSync(helper, 'utf8')

    // A tag checkout leaves a detached HEAD; the next update has no branch
    // to move, so put main back on the release commit.
    assert.match(text, /branch -f main "refs\/tags\/v0\.3\.2"/)
    assert.match(text, /checkout main/)
  } finally {
    try {
      fs.unlinkSync(helper)
    } catch {
      /* already gone */
    }
  }
})

test('the update helper is written into the temp dir, not into the install', () => {
  const helper = writeUpdateHelper({
    installerPath: 'C:\\Temp\\x.exe',
    installDir: 'D:\\Software\\Plobi',
    appPath: 'D:\\Software\\Plobi\\Plobi.exe',
    plobiHome: 'D:\\Data\\AppData\\plobi',
    tag: 'v0.3.2'
  })

  try {
    assert.equal(path.dirname(helper), os.tmpdir())
  } finally {
    try {
      fs.unlinkSync(helper)
    } catch {
      /* already gone */
    }
  }
})
