import assert from 'node:assert/strict'
import test from 'node:test'

import {
  injectSelectionBridge,
  SELECTION_BRIDGE_SOURCE,
  SELECTION_CHANNEL
} from './genoffice-selection-bridge.mjs'

/**
 * GenOffice webview 选区桥：注入到三个 preload 尾部，把 webview 里的选区经
 * sendToHost 发给宿主。needle 断言防上游升级后静默失效（WP-OFFICE-SELECTION-TOOLBAR）。
 */

const PRELOAD = `import { contextBridge, ipcRenderer } from 'electron'
contextBridge.exposeInMainWorld('openDeskOfficeWordHostBridge', {
  notifyDocumentReady: () => ipcRenderer.sendToHost('opendesk-office:document-ready'),
})
`

test('appends a self-contained bridge that uses sendToHost with our channel', () => {
  const out = injectSelectionBridge(PRELOAD)

  assert.ok(out.startsWith(PRELOAD.trimEnd()))
  assert.ok(out.includes(`ipcRenderer.sendToHost('${SELECTION_CHANNEL}'`))
  assert.ok(out.includes('getSelection()'))
  assert.ok(out.includes('getBoundingClientRect()'))
})

test('bridge source does not reference module-level helpers (self-contained)', () => {
  assert.ok(!SELECTION_BRIDGE_SOURCE.includes('SELECTION_CHANNEL'))
  assert.ok(!SELECTION_BRIDGE_SOURCE.includes('injectSelectionBridge'))
})

test('rejects a source with no sendToHost (upstream shape changed)', () => {
  assert.throws(() => injectSelectionBridge('const x = 1'), /no `ipcRenderer.sendToHost`/u)
})

test('rejects double injection', () => {
  const once = injectSelectionBridge(PRELOAD)
  assert.throws(() => injectSelectionBridge(once), /already injected/u)
})

test('rejects a non-string source', () => {
  assert.throws(() => injectSelectionBridge(null), /not a string/u)
})

test('throttles mouseup and guards the try/catch', () => {
  const out = injectSelectionBridge(PRELOAD)

  assert.ok(out.includes("addEventListener('mouseup'"))
  assert.ok(out.includes('clearTimeout'))
  assert.ok(out.includes('catch'))
})
