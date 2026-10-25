import { app, ipcMain, type BrowserWindow, type WebContents } from 'electron'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

interface SheetsHost {
  activateContents: (contents: WebContents) => void
  attachWindow: (window: BrowserWindow) => void
  isSheetsUrl: (url: string) => boolean
  register: () => void
}

interface SheetsHostFactory {
  createOpenDeskOfficeSheetsHost: (options: {
    preloadPath: string
    rendererFilePath: string
    rendererDevUrl?: string
    locale?: string
    ironCalcWasmPath?: string
  }) => SheetsHost
}

const require = createRequire(import.meta.url)
const attachedWindows = new WeakSet<BrowserWindow>()
let host: SheetsHost | null = null
let hostRegistered = false

export function registerGenOffice(mainWindow: BrowserWindow): void {
  if (!host) {
    const appRoot = app.getAppPath()
    const hostPath = path.join(appRoot, 'dist', 'genoffice', 'host.cjs')
    const preloadPath = path.join(appRoot, 'dist', 'genoffice', 'sheets-preload.cjs')

    if (!existsSync(hostPath) || !existsSync(preloadPath)) {
      console.warn('[genoffice] host/preload missing; run scripts/bundle-genoffice.mjs')

      return
    }

    const factory = require(hostPath) as SheetsHostFactory
    const devServer = process.env.PLOBI_DESKTOP_DEV_SERVER
    host = factory.createOpenDeskOfficeSheetsHost({
      preloadPath,
      rendererFilePath: path.join(appRoot, 'dist', 'genoffice', 'sheets.html'),
      ...(devServer ? { rendererDevUrl: 'http://127.0.0.1:5175/sheets.html' } : {}),
      locale: 'zh',
      ironCalcWasmPath: path.join(appRoot, 'dist', 'genoffice', 'ironcalc.wasm')
    })

    if (!hostRegistered) {
      hostRegistered = true
      host.register()
      ipcMain.handle('genoffice:preload-url', () => pathToFileURL(preloadPath).href)

      try {
        ipcMain.handle('app:get-theme', () => 'light')
        ipcMain.handle('disabled:assistant-status', () => ({ disabled: true }))
      } catch {
        // A host or an older shell already owns these channels.
      }
    }
  }

  host.attachWindow(mainWindow)

  if (attachedWindows.has(mainWindow)) {
    return
  }

  attachedWindows.add(mainWindow)
  mainWindow.webContents.on('did-attach-webview', (_event, guest) => {
    const activate = () => {
      if (host && host.isSheetsUrl(guest.getURL())) {
        host.activateContents(guest)
      }
    }

    guest.on('did-finish-load', activate)
    guest.on('did-navigate', activate)
  })
}
