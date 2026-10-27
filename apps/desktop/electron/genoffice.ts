import { app, ipcMain, webContents, type BrowserWindow, type WebContents } from 'electron'
import { existsSync } from 'node:fs'
import { createRequire as nodeCreateRequire } from 'node:module'
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

const sheetsPendingEdits = new Map<number, number>()
const docsDirtyWaiters = new Map<number, (dirty: boolean | null) => void>()
const docsDirtyQueries = new Map<number, Promise<boolean | null>>()
let dirtyListenersRegistered = false

function registerDirtyListeners() {
  if (dirtyListenersRegistered) {
    return
  }

  dirtyListenersRegistered = true

  // The sheets preload reports its pending edit count on every change; the
  // docs renderer answers a close-state query with its dirty flag. Both are
  // the same signals the vendor editors use for their own close guards.
  ipcMain.on('workbook:pending-edits', (event, count: unknown) => {
    if (typeof count === 'number' && Number.isFinite(count) && count >= 0) {
      sheetsPendingEdits.set(event.sender.id, Math.floor(count))
    }
  })
  ipcMain.on('docs:close-check-result', (event, state: unknown) => {
    const waiter = docsDirtyWaiters.get(event.sender.id)

    if (!waiter) {
      return
    }

    docsDirtyWaiters.delete(event.sender.id)
    waiter(Boolean(state && typeof state === 'object' && (state as { dirty?: unknown }).dirty === true))
  })
}

function forgetDirtyState(webContentsId: number) {
  sheetsPendingEdits.delete(webContentsId)
  docsDirtyWaiters.delete(webContentsId)
  docsDirtyQueries.delete(webContentsId)
}

function withTimeout<T>(promise: Promise<T>, fallback: T, timeoutMs = 1800): Promise<T> {
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve(fallback), timeoutMs)

    promise.then(
      value => {
        clearTimeout(timer)
        resolve(value)
      },
      () => {
        clearTimeout(timer)
        resolve(fallback)
      }
    )
  })
}

function queryDocsDirty(contents: WebContents): Promise<boolean | null> {
  const pending = docsDirtyQueries.get(contents.id)

  if (pending) {
    return pending
  }

  const query = new Promise<boolean | null>(resolve => {
    const timer = setTimeout(() => {
      docsDirtyWaiters.delete(contents.id)
      resolve(null)
    }, 2000)

    docsDirtyWaiters.set(contents.id, dirty => {
      clearTimeout(timer)
      resolve(dirty)
    })

    try {
      contents.send('docs:close-check')
    } catch {
      clearTimeout(timer)
      docsDirtyWaiters.delete(contents.id)
      resolve(null)
    }
  }).finally(() => docsDirtyQueries.delete(contents.id))

  docsDirtyQueries.set(contents.id, query)

  return query
}

async function queryDirty(
  kind: GenOfficeKind,
  contents: WebContents
): Promise<boolean | null> {
  if (contents.isDestroyed()) {
    return null
  }

  if (kind === 'sheets') {
    return (sheetsPendingEdits.get(contents.id) ?? 0) > 0
  }

  if (kind === 'slides') {
    return withTimeout(
      contents
        .executeJavaScript('window.slidesApi?.isDirty?.()')
        .then((value: unknown) => (typeof value === 'boolean' ? value : null)),
      null
    )
  }

  return queryDocsDirty(contents)
}

const require = nodeCreateRequire(import.meta.url)
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

  registerDirtyListeners()
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

  // OfficeCLI writes SpreadsheetML with prefixed elements (`<x:...>`). The
  // stock sheets gateway is patched at bundle time (bundle-genoffice.mjs) to
  // strip that prefix before parsing, so no file rewrite is needed here.
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

  ipcMain.handle('genoffice:query-dirty', async (_event, kind: unknown, webContentsId: unknown) => {
    if (kind !== 'slides' && kind !== 'sheets' && kind !== 'word') {
      throw new Error('Unknown GenOffice editor kind')
    }

    const contents = webContents.fromId(Number(webContentsId))

    if (!contents || contents.isDestroyed()) {
      return null
    }

    return queryDirty(kind, contents)
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
    guest.once('destroyed', () => forgetDirtyState(guest.id))
  })
}
