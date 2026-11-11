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
  createReleaseLookup,
  isCompleteDownload,
  parseReleaseTag,
  pickInstallerAsset,
  planDownloadAttempts,
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

test('the update helper installs the shell first, then the agent out of that package', () => {
  const helper = writeUpdateHelper({
    installerPath: 'C:\\Temp\\Plobi-0.3.2-win-x64.exe',
    installDir: 'D:\\Software\\Plobi',
    appPath: 'D:\\Software\\Plobi\\Plobi.exe',
    plobiHome: 'D:\\Data\\AppData\\plobi',
    tag: 'v0.3.2'
  })

  try {
    const text = fs.readFileSync(helper, 'utf8')

    // 1) the shell is replaced by the new installer
    assert.match(text, /Plobi-0\.3\.2-win-x64\.exe" \/S \/D=D:\\Software\\Plobi/)
    // 2) the agent is installed out of THAT package: the bundled backend when
    //    the package carries one (no git, no PyPI), the checkout move
    //    otherwise (shell and agent end up on ONE version number either way)
    assert.match(text, /-BundleDir "D:\\Software\\Plobi\\resources\\backend"/)
    assert.match(text, /-Wheelhouse "D:\\Software\\Plobi\\resources\\wheelhouse"/)
    assert.match(text, /-Tag "v0\.3\.2"/)
    assert.match(text, /install\.ps1/)
    // 3) and the app starts again
    assert.match(text, /start "" "D:\\Software\\Plobi\\Plobi\.exe"/)

    const shellAt = text.indexOf('/S /D=')
    const bundleAt = text.indexOf('-BundleDir')

    assert.ok(shellAt >= 0 && bundleAt > shellAt, 'the agent must be installed AFTER the shell swap (it comes out of that package)')

    // The bundled branch runs the two stages that matter -- copy the source,
    // install the locked wheels -- and NOT the whole script: recreating the
    // venv costs minutes and the node stage would need the network.
    assert.match(text, /-Stage repository -BundleDir/)
    assert.match(text, /-Stage dependencies -BundleDir/)
    assert.doesNotMatch(text, /-Stage venv/)
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
    // FIRST, before the shell swap and before the agent sync.
    assert.match(text, /tasklist \/FI "IMAGENAME eq Plobi\.exe"/)
    assert.match(text, /plobi_wait_exit/)

    const waitAt = text.indexOf('plobi_wait_exit')
    const shellAt = text.indexOf('/S /D=')
    const installAt = text.indexOf('install.ps1')

    assert.ok(waitAt >= 0 && shellAt > waitAt, 'the wait must come before the shell swap')
    assert.ok(installAt > shellAt, 'the agent sync must come after the shell swap')
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

// ---- the release lookup's cache ------------------------------------------
//
// The renderer asks for the latest release on startup, every 30 minutes, on
// every window focus, every time the update sheet opens, every time About
// opens -- and once more right before the install. Each of those used to be
// its own request to api.github.com, whose anonymous budget is 60 per hour
// per IP. Past that budget GitHub answers 403, and curl (without --fail)
// hands back that body as if it were a normal payload -- so the app would
// silently read "no new release" forever.

const payload = (tag: string) =>
  JSON.stringify({
    tag_name: tag,
    html_url: 'https://github.com/deyu-deng/Plobi/releases',
    assets: [{ name: 'Plobi-0.3.2-win-x64.exe', size: 1, browser_download_url: 'https://example.invalid/a.exe' }]
  })

test('the release lookup asks GitHub once and reuses the answer', async () => {
  let calls = 0

  const lookup = createReleaseLookup(async () => {
    calls += 1

    return { status: 200, body: payload('v0.3.2') }
  })

  const first = await lookup.lookup()
  const second = await lookup.lookup()

  assert.equal(calls, 1)
  assert.equal(first.release?.version, '0.3.2')
  assert.equal(second.release?.version, '0.3.2')
})

test('the cached answer goes stale and is fetched again', async () => {
  let now = 0
  let calls = 0

  const lookup = createReleaseLookup(
    async () => {
      calls += 1

      return { status: 200, body: payload('v0.3.2') }
    },
    { now: () => now }
  )

  await lookup.lookup()
  now = 11 * 60 * 1000 // past the 10-minute TTL

  await lookup.lookup()

  assert.equal(calls, 2)
})

test('a failed lookup is retried sooner than a successful one', async () => {
  let now = 0
  let calls = 0

  const lookup = createReleaseLookup(
    async () => {
      calls += 1

      return null
    },
    { now: () => now }
  )

  const first = await lookup.lookup()

  assert.equal(first.reason, 'unreachable')
  assert.equal(first.status, 0)

  now = 30 * 1000 // inside the failure window: don't hammer a dead proxy
  await lookup.lookup()
  assert.equal(calls, 1)

  now = 61 * 1000 // past it: try again
  await lookup.lookup()
  assert.equal(calls, 2)
})

test('a rate-limited answer is told apart from "no release"', async () => {
  const lookup = createReleaseLookup(async () => ({ status: 403, body: '{"message":"rate limit exceeded"}' }))
  const result = await lookup.lookup()

  assert.equal(result.release, null)
  assert.equal(result.reason, 'rate-limited')
  assert.equal(result.status, 403)
})

test('a repository without any release says so', async () => {
  const lookup = createReleaseLookup(async () => ({ status: 404, body: '{"message":"Not Found"}' }))
  const result = await lookup.lookup()

  assert.equal(result.release, null)
  assert.equal(result.reason, 'not-found')
})

test('a date-shaped tag is not a version', async () => {
  const lookup = createReleaseLookup(async () => ({ status: 200, body: payload('v2026.10.20') }))
  const result = await lookup.lookup()

  assert.equal(result.release, null)
  assert.equal(result.reason, 'no-version')
})

test('concurrent lookups share one request', async () => {
  let calls = 0

  const lookup = createReleaseLookup(async () => {
    calls += 1
    await new Promise(resolve => setTimeout(resolve, 10))

    return { status: 200, body: payload('v0.3.2') }
  })

  await Promise.all([lookup.lookup(), lookup.lookup(), lookup.lookup()])

  assert.equal(calls, 1)
})

// ── 整包下载：复用 / 续传 / 尺寸对账（2026-11-10）──────────────────────────
// 安装包是 400 MB 级别（toolchain + wheelhouse 都在里面）。用户点了更新、安装被
// 占着的文件挡回来、再点一次，如果每次都从零拉一遍，这个体积就是纯粹的痛。
// 下面钉住的是"能省就省、但绝不把半截包当成品"。

test('已经有半截文件时才敢用断点续传', () => {
  const fresh = planDownloadAttempts({ hasPartial: false })
  assert.equal(fresh.length, 2)
  assert.ok(fresh.every(a => a.resume === false), '空空如也时 -C - 无从接起')

  const partial = planDownloadAttempts({ hasPartial: true })
  assert.equal(partial.length, 4)
  // 先续（省流量），续不动再全量；每一档都留一次绕代理的机会。
  assert.deepEqual(
    partial.map(a => [a.resume, a.direct]),
    [
      [true, true],
      [true, false],
      [false, true],
      [false, false]
    ]
  )
})

test('尺寸对不上就是没下成 —— 别把 CDN 的错误页递进安装器', () => {
  assert.equal(isCompleteDownload(421 * 1024 * 1024, 421 * 1024 * 1024), true)
  // 半截流
  assert.equal(isCompleteDownload(1024, 421 * 1024 * 1024), false)
  // 比官方还大（旧文件残留 / 续传拼错）
  assert.equal(isCompleteDownload(500 * 1024 * 1024, 421 * 1024 * 1024), false)
  // 文件根本不在
  assert.equal(isCompleteDownload(-1, 421 * 1024 * 1024), false)
  assert.equal(isCompleteDownload(0, 421 * 1024 * 1024), false)
})

test('发布里没给 size 时只要求文件非空 —— 不能因为缺字段把正常下载判死', () => {
  assert.equal(isCompleteDownload(123, 0), true)
  assert.equal(isCompleteDownload(0, 0), false)
})

