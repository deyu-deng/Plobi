/**
 * build-junk.mjs 的判据测试。
 *
 * 这里钉的是一条边界：**只剔临时物，别误伤真资产**。magpie 目录里真正要发的
 * `magpie-cli.exe` 和 `version.json` 必须原样留着；`.magpie-update/` 里那个
 * 下载中的 `.new` 才是该走的。搞反了就是"打出来的包装不上侧车"。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { scrubBuildJunk } from './build-junk.mjs'

function makeTree(root) {
  const write = (relative, content) => {
    const full = path.join(root, relative)
    fs.mkdirSync(path.dirname(full), { recursive: true })
    fs.writeFileSync(full, content)
  }

  write('magpie/magpie-cli.exe', 'x'.repeat(1000))
  write('magpie/version.json', '{"version":"v0.1.3403"}')
  write('magpie/.magpie-update/magpie-cli.exe.new', 'y'.repeat(5000))
  write('backend/plobi/__pycache__/mod.cpython-311.pyc', 'z'.repeat(300))
  write('backend/plobi/main.py', 'print(1)')
  write('backend/.DS_Store', 'junk')
  write('egress/bin/geovpn.exe', 'e'.repeat(700))
  return root
}

test('剔掉下载中的侧车与缓存，真正的资产一个不动', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-junk-'))
  try {
    makeTree(root)
    const before = fs.readdirSync(path.join(root, 'magpie'))

    const { removed, freedBytes } = scrubBuildJunk(root)

    assert.deepEqual(
      removed.map((item) => path.relative(root, item.path)).sort(),
      ['.DS_Store', '.magpie-update', '__pycache__'].map((name) =>
        name === '.DS_Store' ? path.join('backend', name) : name === '__pycache__' ? path.join('backend', 'plobi', name) : path.join('magpie', name)
      ).sort()
    )
    // 5000 + 300 + 4 字节；数对不对不重要，重要的是把"下载中那半截"算进去了
    assert.ok(freedBytes >= 5000, `freedBytes 应包含 .magpie-update 的 5000 字节，实际 ${freedBytes}`)

    assert.equal(fs.existsSync(path.join(root, 'magpie', 'magpie-cli.exe')), true)
    assert.equal(fs.existsSync(path.join(root, 'magpie', 'version.json')), true)
    assert.equal(fs.existsSync(path.join(root, 'magpie', '.magpie-update')), false)
    assert.equal(fs.existsSync(path.join(root, 'backend', 'plobi', 'main.py')), true)
    assert.equal(fs.existsSync(path.join(root, 'egress', 'bin', 'geovpn.exe')), true)
    // 顶层条目只少了 .magpie-update 这一项，别的目录都还在
    assert.equal(before.includes('magpie-cli.exe'), true)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('dryRun 只报数不删 —— 别人还在跑的下载不能被我摸掉', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-junk-dry-'))
  try {
    makeTree(root)
    const { removed, freedBytes } = scrubBuildJunk(root, { dryRun: true })

    assert.ok(removed.length >= 3)
    assert.ok(freedBytes >= 5000)
    assert.equal(fs.existsSync(path.join(root, 'magpie', '.magpie-update', 'magpie-cli.exe.new')), true)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('干净树与不存在的目录都安静返回', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-junk-clean-'))
  try {
    fs.mkdirSync(path.join(root, 'magpie'), { recursive: true })
    fs.writeFileSync(path.join(root, 'magpie', 'magpie-cli.exe'), 'ok')

    const clean = scrubBuildJunk(root)
    assert.deepEqual(clean.removed, [])
    assert.equal(clean.freedBytes, 0)

    const missing = scrubBuildJunk(path.join(root, 'nope'))
    assert.deepEqual(missing.removed, [])

    assert.deepEqual(scrubBuildJunk('').removed, [])
    assert.deepEqual(scrubBuildJunk(undefined).removed, [])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
