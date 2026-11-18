import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import beforePack, { cleanStaleAppOutDir } from '../scripts/before-pack.mjs'

test('cleanStaleAppOutDir removes a populated unpacked directory', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-before-pack-'))
  try {
    const appOutDir = path.join(tempRoot, 'linux-unpacked')
    fs.mkdirSync(appOutDir, { recursive: true })
    // Reproduce the corrupted partial state: license + payload present,
    // electron binary missing — exactly what trips the ENOENT rename.
    fs.writeFileSync(path.join(appOutDir, 'LICENSE.electron.txt'), 'x', 'utf8')
    fs.writeFileSync(path.join(appOutDir, 'resources.pak'), 'x', 'utf8')
    fs.mkdirSync(path.join(appOutDir, 'resources'), { recursive: true })
    fs.writeFileSync(path.join(appOutDir, 'resources', 'app.asar'), 'x', 'utf8')

    const removed = cleanStaleAppOutDir(appOutDir)

    assert.equal(removed, true)
    assert.equal(fs.existsSync(appOutDir), false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir is a no-op when the directory is absent', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-before-pack-'))
  try {
    const missing = path.join(tempRoot, 'does-not-exist')
    assert.equal(cleanStaleAppOutDir(missing), false)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('cleanStaleAppOutDir ignores empty or invalid input', () => {
  assert.equal(cleanStaleAppOutDir(''), false)
  assert.equal(cleanStaleAppOutDir(undefined), false)
  assert.equal(cleanStaleAppOutDir(null), false)
  assert.equal(cleanStaleAppOutDir(42), false)
})

test('beforePack default export resolves even when cleanup throws', async () => {
  // A directory path that rmSync can't remove is simulated by passing a
  // context whose appOutDir is a file the hook will try (and be allowed) to
  // remove; the contract under test is that the hook never rejects.
  const saved = process.env.PLOBI_RELEASE_BUILD
  delete process.env.PLOBI_RELEASE_BUILD

  try {
    await assert.doesNotReject(beforePack({ appOutDir: '', electronPlatformName: 'linux' }))
  } finally {
    if (saved !== undefined) {
      process.env.PLOBI_RELEASE_BUILD = saved
    }
  }
})

test('beforePack fails the release build when the routing engine is missing', async () => {
  // 0.3.2 发的就是"缺件只警告"的包：装完点开关得到 `engine binary " " is not on disk`。
  // 发版路径（PLOBI_RELEASE_BUILD=1）必须当场炸，而不是把 MISSING.txt 塞进安装包。
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-before-pack-release-'))
  const emptySource = path.join(tempRoot, 'empty-egress-src')
  const savedSource = process.env.PLOBI_EGRESS_SRC
  const savedRelease = process.env.PLOBI_RELEASE_BUILD

  fs.mkdirSync(emptySource, { recursive: true })
  process.env.PLOBI_EGRESS_SRC = emptySource
  process.env.PLOBI_RELEASE_BUILD = '1'

  try {
    await assert.rejects(
      beforePack({ appOutDir: '', electronPlatformName: 'linux' }),
      /发版构建不许出缺件的包/
    )
  } finally {
    if (savedSource === undefined) {
      delete process.env.PLOBI_EGRESS_SRC
    } else {
      process.env.PLOBI_EGRESS_SRC = savedSource
    }

    if (savedRelease === undefined) {
      delete process.env.PLOBI_RELEASE_BUILD
    } else {
      process.env.PLOBI_RELEASE_BUILD = savedRelease
    }

    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
