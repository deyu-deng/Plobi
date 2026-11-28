import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, describe, it } from 'node:test'

import {
  findPluginUiRoot,
  pluginAssetCsp,
  pluginProtocolPrivileges,
  resolvePluginAsset
} from './plugin-protocol'

/**
 * 插件界面的**独立 origin**（深档机制第三刀 · 规格 ``Docs/specs/plugin-sandbox.md`` §2.3）。
 *
 * 判据是**关系式**的：这一格只发**该发的**（白名单类型）、**发不出去越界的**（词法越界与
 * 软链越界都拒）、**带着不可放宽的 CSP**。
 */

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-plugin-ui-'))

after(() => {
  fs.rmSync(scratch, { recursive: true, force: true })
})

function makeUiDir(files: Record<string, string>): string {
  const root = fs.mkdtempSync(path.join(scratch, 'plugin-'))
  const ui = path.join(root, 'ui')
  fs.mkdirSync(ui, { recursive: true })

  for (const [name, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(ui, name)), { recursive: true })
    fs.writeFileSync(path.join(ui, name), content, 'utf8')
  }

  return ui
}

describe('插件界面的 scheme', () => {
  it('是独立 origin ＋ 安全特权（secure ＋ standard ＋ supportFetchAPI）', () => {
    const privileges = pluginProtocolPrivileges()

    assert.equal(privileges.secure, true)
    assert.equal(privileges.standard, true)
    assert.equal(privileges.supportFetchAPI, true)
  })

  it('CSP **不可放宽**：脚本只准同源文件、连接一律断掉', () => {
    const csp = pluginAssetCsp()

    assert.match(csp, /script-src 'self'/)
    assert.ok(!csp.includes("script-src 'self' 'unsafe-eval'"))
    assert.ok(!csp.includes("'unsafe-inline' 'unsafe-eval'"))
    assert.match(csp, /connect-src 'none'/)
    assert.match(csp, /default-src 'none'/)
  })
})

describe('界面产物：只发该发的', () => {
  it('白名单里的类型发得出去（带内容类型）', () => {
    const ui = makeUiDir({ 'index.html': '<!doctype html><title>x</title>' })

    const asset = resolvePluginAsset(ui, '/index.html')

    assert.ok(asset)
    assert.equal(asset.contentType, 'text/html; charset=utf-8')
    assert.equal(fs.readFileSync(asset.filePath, 'utf8').includes('<title>'), true)
  })

  it('目录 ⇒ index.html', () => {
    const ui = makeUiDir({ 'index.html': 'x' })

    const asset = resolvePluginAsset(ui, '/')

    assert.ok(asset)
    assert.ok(asset.filePath.endsWith('index.html'))
  })

  it('不在白名单里的类型**不发**（那是可执行的东西，不是静态产物）', () => {
    const ui = makeUiDir({ 'app.exe': 'binary' })

    assert.equal(resolvePluginAsset(ui, '/app.exe'), null)
  })

  it('不存在的文件 ⇒ 没有', () => {
    const ui = makeUiDir({ 'index.html': 'x' })

    assert.equal(resolvePluginAsset(ui, '/missing.html'), null)
  })
})

describe('界面产物：不许跑出 ui/ 那一格', () => {
  it('词法越界（..）拒', () => {
    const ui = makeUiDir({ 'index.html': 'x' })

    assert.equal(resolvePluginAsset(ui, '/../secret.txt'), null)
    assert.equal(resolvePluginAsset(ui, '/a/../../secret.txt'), null)
  })

  it('软链越界也拒（resolve 不跟软链走 ⇒ 这里再解析一次真实路径）', () => {
    const outside = path.join(scratch, 'outside.txt')
    fs.writeFileSync(outside, 'secret', 'utf8')
    const ui = makeUiDir({ 'index.html': 'x' })

    try {
      fs.symlinkSync(outside, path.join(ui, 'link.txt'))
    } catch {
      // 这台机器没有软链特权 ⇒ 跳过这一条（那正是 Windows 的既有形状）。
      return
    }

    assert.equal(resolvePluginAsset(ui, '/link.txt'), null)
  })
})

describe('那个插件的界面产物在哪一格', () => {
  it('按 roots 顺序找（用户那份先，随包那份后）', () => {
    const userRoot = fs.mkdtempSync(path.join(scratch, 'user-plugins-'))
    const bundledRoot = fs.mkdtempSync(path.join(scratch, 'bundled-plugins-'))
    fs.mkdirSync(path.join(userRoot, 'my-thing', 'ui'), { recursive: true })
    fs.mkdirSync(path.join(bundledRoot, 'other-thing', 'ui'), { recursive: true })

    assert.equal(
      findPluginUiRoot([userRoot, bundledRoot], 'my-thing'),
      path.join(userRoot, 'my-thing', 'ui')
    )
    assert.equal(
      findPluginUiRoot([userRoot, bundledRoot], 'other-thing'),
      path.join(bundledRoot, 'other-thing', 'ui')
    )
    assert.equal(findPluginUiRoot([userRoot, bundledRoot], 'nope'), null)
  })

  it('目录名里带分隔符 ⇒ 拒（不许借它跑到别处去）', () => {
    assert.equal(findPluginUiRoot([scratch], '../..'), null)
    assert.equal(findPluginUiRoot([scratch], 'a/b'), null)
  })
})
