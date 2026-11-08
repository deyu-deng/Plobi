import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveAppUserModelId, resolveNotificationActivation } from './app-identity'

test('the packaged app claims the product AUMID', () => {
  assert.equal(
    resolveAppUserModelId({
      isWindows: true,
      isDefaultApp: false,
      productAumid: 'com.plobi.desktop'
    }),
    'com.plobi.desktop'
  )
})

test('the dev tree does NOT claim the product AUMID', () => {
  // 开发态跑的是 dev 树的 electron.exe。占了产品那个 AUMID，点通知就会唤起一个
  // 不带 app 路径的 electron.exe ⇒ 弹出 Electron 的默认窗口（2026-11-08 实锤）。
  assert.equal(
    resolveAppUserModelId({
      isWindows: true,
      isDefaultApp: true,
      productAumid: 'com.plobi.desktop'
    }),
    null
  )
})

test('macOS and Linux never set an AUMID', () => {
  assert.equal(
    resolveAppUserModelId({
      isWindows: false,
      isDefaultApp: false,
      productAumid: 'com.plobi.desktop'
    }),
    null
  )
})

test('clicking a notification focuses the window when there is one', () => {
  assert.equal(resolveNotificationActivation(true), 'focus')
})

test('clicking a notification reopens the app when the window is gone', () => {
  assert.equal(resolveNotificationActivation(false), 'reopen')
})
