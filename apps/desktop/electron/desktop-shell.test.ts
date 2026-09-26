/**
 * Unit tests for the desktop shell lifecycle policy. These cover the two
 * promises this slice makes to the user: closing the window must NOT stop the
 * backend (but picking Quit must), and an autostart launch must land in the
 * tray. Pure helpers only — no Electron.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDevAutostartScript,
  DEV_AUTOSTART_FILENAME,
  mergeShellSettings,
  readShellSettings,
  SHELL_SETTINGS_DEFAULTS,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
  startHiddenFromLaunch,
  trayLanguage,
  trayStrings
} from './desktop-shell'

// ─── shouldHideOnClose ─────────────────────────────────────────────────────

test('closing the window hides it while the app is just running normally', () => {
  assert.equal(shouldHideOnClose({}), true)
  assert.equal(shouldHideOnClose({ isQuittingByUser: false, isQuittingForHandoff: false }), true)
})

test('an explicit user quit must really close the window', () => {
  assert.equal(shouldHideOnClose({ isQuittingByUser: true }), false)
})

test('a hand-off to the updater must really close the window', () => {
  assert.equal(shouldHideOnClose({ isQuittingForHandoff: true }), false)
})

test('a failed tray falls back to really closing, not hiding with no way back', () => {
  assert.equal(shouldHideOnClose({ hasTray: false }), false)
})

// ─── shouldQuitOnAllWindowsClosed ──────────────────────────────────────────

test('Windows keeps the process alive when the last window is gone', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: false }), false)
})

test('Linux keeps the process alive too', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: false }), false)
})

test('macOS keeps its Dock convention: alive unless handing off', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: true }), false)
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: true, isQuittingByUser: true }), false)
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: true, isQuittingForHandoff: true }), true)
})

test('user quit quits on Windows/Linux even though close would have hidden', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: false, isQuittingByUser: true }), true)
})

test('updater hand-off quits on every platform', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: false, isQuittingForHandoff: true }), true)
})

test('with no tray there is nothing keeping the app reachable, so it quits', () => {
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: false, hasTray: false }), true)
  // macOS still keeps its Dock convention — no tray icon needed there.
  assert.equal(shouldQuitOnAllWindowsClosed({ isMac: true, hasTray: false }), false)
})

// ─── startHiddenFromLaunch ─────────────────────────────────────────────────

test('--hidden (packaged autostart) boots into the tray', () => {
  assert.equal(startHiddenFromLaunch({ argv: ['Plobi.exe', '--hidden'] }), true)
})

test('the dev Startup script signals the tray boot through the environment', () => {
  assert.equal(startHiddenFromLaunch({ env: { PLOBI_DESKTOP_START_HIDDEN: '1' } }), true)
})

test('an explicit off value does not hide the window', () => {
  assert.equal(startHiddenFromLaunch({ env: { PLOBI_DESKTOP_START_HIDDEN: '0' } }), false)
  assert.equal(startHiddenFromLaunch({ env: { PLOBI_DESKTOP_START_HIDDEN: '' } }), false)
})

test('a normal launch shows the window', () => {
  assert.equal(startHiddenFromLaunch({ argv: ['Plobi.exe'], env: {} }), false)
  assert.equal(startHiddenFromLaunch(), false)
})

// ─── shell settings (stored in window-state.json) ──────────────────────────

test('autostart defaults ON and the balloon defaults unshown', () => {
  assert.deepEqual(readShellSettings(), SHELL_SETTINGS_DEFAULTS)
  assert.deepEqual(readShellSettings({}), SHELL_SETTINGS_DEFAULTS)
  assert.deepEqual(readShellSettings(null), SHELL_SETTINGS_DEFAULTS)
  assert.equal(SHELL_SETTINGS_DEFAULTS.openAtLogin, true)
})

test('only an explicit false turns autostart off', () => {
  assert.equal(readShellSettings({ openAtLogin: false }).openAtLogin, false)
  assert.equal(readShellSettings({ openAtLogin: 'nope' }).openAtLogin, true)
})

test('the balloon flag survives a round trip', () => {
  assert.equal(readShellSettings({ trayBalloonShown: true }).trayBalloonShown, true)
  assert.equal(readShellSettings({ trayBalloonShown: 'yes' }).trayBalloonShown, false)
})

test('merging keeps the geometry so the two writers share one file', () => {
  const raw = { x: 10, y: 20, width: 1220, height: 800, isMaximized: false }

  assert.deepEqual(mergeShellSettings(raw, { openAtLogin: false }), {
    x: 10,
    y: 20,
    width: 1220,
    height: 800,
    isMaximized: false,
    openAtLogin: false
  })
})

test('merging keeps the shell flags when geometry is re-written', () => {
  const raw = { openAtLogin: false, trayBalloonShown: true }

  assert.deepEqual(mergeShellSettings(raw, {}), { openAtLogin: false, trayBalloonShown: true })
})

test('merging ignores junk input and non-boolean patches', () => {
  assert.deepEqual(mergeShellSettings('garbage', { openAtLogin: true }), { openAtLogin: true })
  // A hand-edited / corrupt settings file must not be trusted.
  assert.deepEqual(mergeShellSettings(null, { openAtLogin: 'yes' } as any), {})
})

// ─── tray strings ──────────────────────────────────────────────────────────

test('Chinese is the primary tray language and English is the fallback', () => {
  assert.equal(trayLanguage('zh-CN'), 'zh')
  assert.equal(trayLanguage(undefined), 'zh')
  assert.equal(trayLanguage(''), 'zh')
  assert.equal(trayStrings('zh-CN').show, '显示 Plobi')

  assert.equal(trayLanguage('en-US'), 'en')
  assert.equal(trayStrings('en-US').show, 'Show Plobi')
})

test('every tray string is present in both languages', () => {
  for (const locale of ['zh-CN', 'en-US']) {
    const strings = trayStrings(locale)

    for (const [key, value] of Object.entries(strings)) {
      assert.ok(typeof value === 'string' && value.length > 0, `${locale}.${key} must be non-empty`)
    }
  }
})

// ─── dev autostart script ──────────────────────────────────────────────────

test('the dev autostart script relaunches hidden in the right working directory', () => {
  const script = buildDevAutostartScript({
    workingDirectory: 'D:\\Projects\\Plobi\\Code\\apps\\desktop',
    command: 'cmd /c npm run dev',
    environment: { PLOBI_DESKTOP_START_HIDDEN: '1' }
  })

  assert.ok(script.includes('sh.CurrentDirectory = "D:\\Projects\\Plobi\\Code\\apps\\desktop"'))
  assert.ok(script.includes('sh.Run "cmd /c npm run dev", 0, False'))
  assert.ok(script.includes('env.Item("PLOBI_DESKTOP_START_HIDDEN") = "1"'))
  // Window style 0 is what keeps the console from flashing on every boot.
  assert.ok(script.includes(', 0, False'))
  assert.ok(script.includes('Option Explicit'))
})

test('the dev autostart script is CRLF-terminated for WScript', () => {
  const script = buildDevAutostartScript({ workingDirectory: 'C:\\x', command: 'y' })

  assert.ok(script.includes('\r\n'))
  assert.ok(script.endsWith('\r\n'))
  assert.ok(!/(?<!\r)\n/.test(script), 'no bare LF line endings')
  assert.equal(DEV_AUTOSTART_FILENAME, 'plobi_desktop_autostart.vbs')
})
