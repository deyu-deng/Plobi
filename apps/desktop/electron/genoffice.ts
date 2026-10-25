import { app, ipcMain, type BrowserWindow, type WebContents } from 'electron'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

type GenOfficeKind = 'slides' | 'sheets' | 'word'

interface GenOfficeHost {
  activateContents: (contents: WebContents) => void
  attachWindow: (window: BrowserWindow) => void
  register: () => void
  isSheetsUrl?: (url: string) => boolean
  isSlidesUrl?: (url: string) => boolean
  isWordUrl?: (url: string) => boolean
}

interface HostFactory {
  createOpenDeskOfficeSheetsHost: (options: Record<string, unknown>) => GenOfficeHost
  createOpenDeskOfficeSlidesHost: (options: Record<string, unknown>) => GenOfficeHost
  createOpenDeskOfficeWordHost: (options: Record<string, unknown>) => GenOfficeHost
}

const require = createRequire(import.meta.url)
const attachedWindows = new WeakSet<BrowserWindow>()
const hosts: Partial<Record<GenOfficeKind, GenOfficeHost>> = {}
let registeredHosts = false

const PAGES: Record<GenOfficeKind, string> = {
  slides: 'slides.html',
  sheets: 'sheets.html',
  word: 'word.html'
}

function preloadPath(appRoot: string, kind: GenOfficeKind): string {
  return path.join(appRoot, 'dist', 'genoffice', `${kind}-preload.cjs`)
}

function registerHosts(): boolean {
  if (registeredHosts) {
    return true
  }

  const appRoot = app.getAppPath()
  const hostPath = path.join(appRoot, 'dist', 'genoffice', 'host.cjs')

  if (!existsSync(hostPath)) {
    console.warn('[genoffice] host bundle missing; run scripts/bundle-genoffice.mjs')

    return false
  }

  const devServer = process.env.PLOBI_DESKTOP_DEV_SERVER
  const factory = require(hostPath) as HostFactory
  const base = (kind: GenOfficeKind) => ({
    preloadPath: preloadPath(appRoot, kind),
    rendererFilePath: path.join(appRoot, 'dist', 'genoffice', PAGES[kind]),
    ...(devServer ? { rendererDevUrl: `http://127.0.0.1:5175/${PAGES[kind]}` } : {}),
    locale: 'zh'
  })

  hosts.sheets = factory.createOpenDeskOfficeSheetsHost({
    ...base('sheets'),
    ironCalcWasmPath: path.join(appRoot, 'dist', 'genoffice', 'ironcalc.wasm')
  })
  hosts.word = factory.createOpenDeskOfficeWordHost(base('word'))
  hosts.slides = factory.createOpenDeskOfficeSlidesHost(base('slides'))

  for (const host of Object.values(hosts)) {
    host.register()
  }

  registeredHosts = true
  ipcMain.handle('genoffice:preload-url', (_event, kind: unknown) => {
    if (kind !== 'slides' && kind !== 'sheets' && kind !== 'word') {
      throw new Error('Unknown GenOffice editor kind')
    }

    return pathToFileURL(preloadPath(app.getAppPath(), kind)).href
  })

  try {
    ipcMain.handle('app:get-theme', () => 'light')
    ipcMain.handle('disabled:assistant-status', () => ({ disabled: true }))
  } catch {
    // Another desktop surface already owns these channels.
  }

  return true
}

export function registerGenOffice(mainWindow: BrowserWindow): void {
  if (!registerHosts()) {
    return
  }

  for (const host of Object.values(hosts)) {
    host.attachWindow(mainWindow)
  }

  if (attachedWindows.has(mainWindow)) {
    return
  }

  attachedWindows.add(mainWindow)
  mainWindow.webContents.on('did-attach-webview', (_event, guest) => {
    const activate = () => {
      const url = guest.getURL()

      for (const [kind, host] of Object.entries(hosts) as [GenOfficeKind, GenOfficeHost][]) {
        const isEditorUrl =
          kind === 'sheets' ? host.isSheetsUrl : kind === 'word' ? host.isWordUrl : host.isSlidesUrl

        if (isEditorUrl?.(url)) {
          host.activateContents(guest)
        }
      }
    }

    guest.on('did-finish-load', activate)
    guest.on('did-navigate', activate)
  })
}
