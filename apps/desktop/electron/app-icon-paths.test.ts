/**
 * Tests for electron/app-icon-paths.ts — WP-ICON-FULL-SWEEP 的那颗牙。
 *
 * Run with: npm run test:desktop:platforms
 *
 * 钉的是用户 11-07 任务栏截图那个形状：Windows 的**窗口**图标不吃 PNG，喂 PNG 会被
 * 静默忽略、任务栏回落到 electron.exe 内嵌的默认原子。所以 win32 的候选表里 .ico
 * 必须排在所有 PNG 之前（调用方取第一个存在的）；mac/linux 不吃 .ico，表里不许有它。
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import { appIconPaths } from './app-icon-paths'

const ROOT = '/repo/apps/desktop'
const UNPACKED = '/repo/apps/desktop.asar.unpacked'

function paths(isWindows: boolean, resourcesPath?: string) {
  return appIconPaths({
    isWindows,
    appRoot: ROOT,
    resourcesPath,
    unpackedRoot: UNPACKED,
    join: (...p: string[]) => path.posix.join(...p)
  })
}

test('win32: .ico 排在所有 PNG 之前，源码态与装机态两条都在', () => {
  const p = paths(true, '/opt/Plobi/resources')
  assert.equal(p[0], path.posix.join(ROOT, 'assets', 'icon.ico'))
  assert.equal(p[1], '/opt/Plobi/resources/icon.ico')
  const firstPng = p.findIndex((x) => x.endsWith('.png'))
  assert.ok(firstPng > 1, 'PNG 不许抢在 .ico 前面')
  assert.ok(p.slice(0, firstPng).every((x) => x.endsWith('.ico')))
})

test('win32 源码态（没有 resourcesPath）：仍有一条 .ico 打头', () => {
  const p = paths(true)
  assert.equal(p[0], path.posix.join(ROOT, 'assets', 'icon.ico'))
  assert.ok(!p.some((x) => x.includes('resources/icon.ico')))
})

test('mac/linux：表里不许出现 .ico，顺序保持 public → dist → unpacked', () => {
  for (const win of [false]) {
    const p = paths(win, '/opt/Plobi/resources')
    assert.ok(!p.some((x) => x.endsWith('.ico')), '非 win32 不该有 .ico')
    assert.deepEqual(p, [
      path.posix.join(ROOT, 'public', 'apple-touch-icon.png'),
      path.posix.join(ROOT, 'dist', 'apple-touch-icon.png'),
      path.posix.join(UNPACKED, 'dist', 'apple-touch-icon.png')
    ])
  }
})

test('两个平台都保留 PNG 兜底：.ico 缺失时不至于拿不到任何图', () => {
  for (const win of [true, false]) {
    const p = paths(win)
    assert.ok(p.some((x) => x.endsWith('apple-touch-icon.png')))
  }
})
