/**
 * Tests for electron/app-icon-paths.ts — WP-ICON-FULL-SWEEP 的那颗牙。
 *
 * Run with: npm run test:desktop:platforms
 *
 * 钉的是用户 11-07 任务栏截图那个形状：Windows 的**窗口**图标不吃 PNG，喂 PNG 会被
 * 静默忽略、任务栏回落到 electron.exe 内嵌的默认原子。所以 win32 的候选表里 .ico
 * 必须排在所有 PNG 之前（调用方取第一个存在的）。
 *
 * 11-11 之后又加了一层：三个平台各有各的规范，不能一张图通吃。
 *   - darwin：Dock/setIcon 会把图直接铺满 48pt 方框，必须用 824/1024 带留白的
 *     `icon-macos.png`；
 *   - win32：.ico（多尺寸），满幅；
 *   - linux：freedesktop 惯例是满幅 `icon-linux.png`，桌面环境自己裁，自带留白反而显小。
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import { appIconPaths, trayIconPaths } from './app-icon-paths'

const ROOT = '/repo/apps/desktop'
const UNPACKED = '/repo/apps/desktop.asar.unpacked'

function paths(platform: string, resourcesPath?: string) {
  return appIconPaths({
    platform,
    appRoot: ROOT,
    resourcesPath,
    unpackedRoot: UNPACKED,
    join: (...p: string[]) => path.posix.join(...p)
  })
}

test('win32: .ico 排在所有 PNG 之前，源码态与装机态两条都在', () => {
  const p = paths('win32', '/opt/Plobi/resources')
  assert.equal(p[0], path.posix.join(ROOT, 'assets', 'icon.ico'))
  assert.equal(p[1], '/opt/Plobi/resources/icon.ico')
  const firstPng = p.findIndex((x) => x.endsWith('.png'))
  assert.ok(firstPng > 1, 'PNG 不许抢在 .ico 前面')
  assert.ok(p.slice(0, firstPng).every((x) => x.endsWith('.ico')))
})

test('win32 源码态（没有 resourcesPath）：仍有一条 .ico 打头', () => {
  const p = paths('win32')
  assert.equal(p[0], path.posix.join(ROOT, 'assets', 'icon.ico'))
  assert.ok(!p.some((x) => x.includes('resources/icon.ico')))
})

test('darwin：表里不许出现 .ico，首选项是留过边距的 icon-macos.png', () => {
  const p = paths('darwin', '/opt/Plobi/resources')
  assert.ok(!p.some((x) => x.endsWith('.ico')), '非 win32 不该有 .ico')
  // Dock 会 setIcon 传进来的图直接铺满 48pt 方框：满幅的 touch icon 排首位就是
  // 用户截图里那个直角大块，所以首位必须是 824/1024 带留白的 macOS 版。
  assert.deepEqual(p, [
    path.posix.join(ROOT, 'assets', 'icon-macos.png'),
    '/opt/Plobi/resources/icon-macos.png',
    path.posix.join(ROOT, 'public', 'apple-touch-icon.png'),
    path.posix.join(ROOT, 'dist', 'apple-touch-icon.png'),
    path.posix.join(UNPACKED, 'dist', 'apple-touch-icon.png')
  ])
})

test('linux：满幅 icon-linux.png 排首位，且不许混进 macOS 那套留白图', () => {
  const p = paths('linux', '/opt/Plobi/resources')
  assert.equal(p[0], path.posix.join(ROOT, 'assets', 'icon-linux.png'))
  assert.equal(p[1], '/opt/Plobi/resources/icon-linux.png')
  assert.ok(!p.some((x) => x.endsWith('.ico')), 'linux 不该有 .ico')
  assert.ok(
    !p.some((x) => x.includes('icon-macos')),
    'linux 用满幅图；824/1024 的留白版是 Apple 专用，塞过去会显小一圈'
  )
})

test('三平台互不吃对方的专属图', () => {
  const win = paths('win32')
  const mac = paths('darwin')
  const linux = paths('linux')

  assert.ok(!win.some((x) => x.includes('icon-macos')))
  assert.ok(!mac.some((x) => x.endsWith('icon.ico')))
  assert.ok(!linux.some((x) => x.endsWith('icon.ico')))
  assert.notEqual(mac[0], linux[0], 'mac 与 linux 的首选项必须是两张不同的图')
})

test('三个平台都保留 PNG 兜底：专属图缺失时不至于拿不到任何图', () => {
  for (const platform of ['win32', 'darwin', 'linux']) {
    const p = paths(platform)
    assert.ok(
      p.some((x) => x.endsWith('apple-touch-icon.png')),
      `${platform} 缺兜底`
    )
  }
})

// --- macOS 菜单栏（template image）-----------------------------------------
//
// 用户 11-11 的截图：菜单栏那一排全是单色剪影，只有 Plobi 是一块带橙色底的行货
// app icon，视觉上大一圈。Apple 的规矩是 template image —— 黑色 + alpha，系统负责
// 亮/暗反相；彩色满幅的应用图标进菜单栏就是不合规。钉住两条：
//   1) 菜单栏路径只能指向 trayTemplate*.png，绝不回落 apple-touch-icon.png；
//   2) base 与 badge 是两张不同的图（异常与否靠换图表达，不靠彩色角标）。

test('trayIconPaths: base / badge 指向不同的模板图，且都不指向彩色的 app icon', () => {
  const base = trayIconPaths({ appRoot: ROOT, resourcesPath: '/opt/Plobi/resources', join: (...p: string[]) => path.posix.join(...p) }, 'base')
  const badge = trayIconPaths({ appRoot: ROOT, resourcesPath: '/opt/Plobi/resources', join: (...p: string[]) => path.posix.join(...p) }, 'badge')

  assert.equal(base[0], path.posix.join(ROOT, 'assets', 'trayTemplate.png'))
  assert.equal(base[1], '/opt/Plobi/resources/trayTemplate.png')
  assert.equal(badge[0], path.posix.join(ROOT, 'assets', 'trayTemplateBadge.png'))
  assert.notEqual(base[0], badge[0], 'badge 必须是另一张图')
  assert.ok(![...base, ...badge].some((p) => p.includes('apple-touch-icon')), '彩色满幅图不许进菜单栏候选表')
})

test('trayIconPaths: 无 resourcesPath（源码态）时只剩 assets 一条', () => {
  const p = trayIconPaths({ appRoot: ROOT, join: (...parts: string[]) => path.posix.join(...parts) }, 'base')
  assert.deepEqual(p, [path.posix.join(ROOT, 'assets', 'trayTemplate.png')])
})
