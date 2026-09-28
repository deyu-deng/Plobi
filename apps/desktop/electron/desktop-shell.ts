/**
 * Pure lifecycle policy for the desktop shell — the decisions that keep the
 * Python backend, cron and the chatlog/aigw sidecars alive after the user
 * closes the window, and the small amount of state the tray + autostart
 * toggle need. Side-effect-free so these user-facing guarantees are
 * unit-testable without booting Electron; main.ts owns Tray, BrowserWindow
 * and all file I/O.
 */

// ─── Quit policy ───────────────────────────────────────────────────────────

interface QuitFlags {
  isQuittingByUser?: boolean
  isQuittingForHandoff?: boolean
}

/**
 * Closing the primary window parks it in the tray instead of ending the
 * process, so everything the desktop spawned (backend, cron, chatlog, aigw)
 * keeps running. Three things override that: an explicit user quit; a hand-off
 * to a detached installer/updater script, which needs this process — and the
 * file locks it holds — gone before it can swap the bundle; and a missing tray,
 * because hiding with no tray icon would leave a window the user cannot get
 * back and a process they cannot quit.
 */
export function shouldHideOnClose({
  isQuittingByUser,
  isQuittingForHandoff,
  hasTray = true
}: QuitFlags & { hasTray?: boolean } = {}): boolean {
  return Boolean(hasTray) && !isQuittingByUser && !isQuittingForHandoff
}

/**
 * macOS keeps the process alive when the last window closes (Dock convention);
 * Windows/Linux now do the same so the tray outlives the window. A stranded
 * headless process is the failure mode to avoid, so an explicit quit must
 * still quit on every platform — and with no tray there is nothing keeping the
 * app reachable, so the old "last window closed = quit" behavior stands.
 */
export function shouldQuitOnAllWindowsClosed({
  isMac,
  isQuittingByUser,
  isQuittingForHandoff,
  hasTray = true
}: QuitFlags & { isMac?: boolean; hasTray?: boolean } = {}): boolean {
  if (isMac) {
    return Boolean(isQuittingForHandoff)
  }

  if (!hasTray) {
    return true
  }

  return Boolean(isQuittingByUser || isQuittingForHandoff)
}

/**
 * Boot straight into the tray. A packaged autostart passes `--hidden`; the dev
 * Startup script sets PLOBI_DESKTOP_START_HIDDEN=1 instead, because `npm run
 * dev` goes through concurrently and there is no clean way to append an argv
 * flag to the Electron child. The window is still created and the renderer
 * still loads — it just never shows until the user picks "Show" from the tray.
 */
export function startHiddenFromLaunch({ argv, env }: { argv?: string[]; env?: Record<string, string | undefined> } = {}): boolean {
  if (Array.isArray(argv) && argv.includes('--hidden')) {
    return true
  }

  const flag = env?.PLOBI_DESKTOP_START_HIDDEN

  return Boolean(flag) && flag !== '0' && flag.toLowerCase() !== 'false'
}

// ─── Shell settings (tray + autostart) ─────────────────────────────────────

export interface ShellSettings {
  openAtLogin: boolean
  trayBalloonShown: boolean
}

// Autostart defaults ON: the whole point of this slice is that a cold boot
// brings Plobi (and its backend) up without the user clicking anything.
export const SHELL_SETTINGS_DEFAULTS: ShellSettings = { openAtLogin: true, trayBalloonShown: false }

/**
 * Read the shell flags out of the existing window-state.json object. Kept
 * deliberately lenient: anything that isn't an explicit `false` means
 * autostart is on, so a hand-edited or partially written file can't silently
 * drop the user back to "Plobi doesn't start".
 */
export function readShellSettings(raw?: unknown): ShellSettings {
  const obj = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {}

  return {
    openAtLogin: obj.openAtLogin !== false,
    trayBalloonShown: obj.trayBalloonShown === true
  }
}

/**
 * Merge a patch into the raw window-state object instead of replacing it. The
 * geometry writer and the shell-flag writer share one file, so each must
 * preserve the other's keys rather than racing to own the whole document.
 */
export function mergeShellSettings(raw: unknown, patch: Partial<ShellSettings> = {}): Record<string, unknown> {
  const base = raw && typeof raw === 'object' && !Array.isArray(raw) ? { ...(raw as Record<string, unknown>) } : {}

  if (typeof patch.openAtLogin === 'boolean') {
    base.openAtLogin = patch.openAtLogin
  }

  if (typeof patch.trayBalloonShown === 'boolean') {
    base.trayBalloonShown = patch.trayBalloonShown
  }

  return base
}

// ─── Tray labels ───────────────────────────────────────────────────────────

export type TrayLanguage = 'zh' | 'en'

export interface TrayStrings {
  tooltip: string
  show: string
  openAtLogin: string
  quit: string
  balloonTitle: string
  balloonBody: string
  /**
   * R-051 — heading of the deferred-subsystem submenu. The rows underneath come
   * from the main-process ledger (electron/deferred-sidecars.ts), which is the
   * SAME array the boot-progress payload renders — this string only labels it.
   */
  deferredGroup: string
}

// The Electron main process has no i18n layer of its own (only the renderer
// does) and this slice may not add a dependency, so tray strings are a tiny
// local table: Chinese primary, English when the OS locale explicitly asks for
// it.
const TRAY_STRINGS: Record<TrayLanguage, TrayStrings> = {
  zh: {
    tooltip: 'Plobi',
    show: '显示 Plobi',
    openAtLogin: '开机自启',
    quit: '退出 Plobi',
    balloonTitle: 'Plobi 仍在运行',
    balloonBody: '窗口已收进托盘，后台服务继续运行。',
    deferredGroup: '后置服务（R-047 延后，非故障）'
  },
  en: {
    tooltip: 'Plobi',
    show: 'Show Plobi',
    openAtLogin: 'Launch at login',
    quit: 'Quit Plobi',
    balloonTitle: 'Plobi is still running',
    balloonBody: 'The window is in the tray; background services keep running.',
    deferredGroup: 'Deferred services (postponed, not faults)'
  }
}

export function trayLanguage(locale?: string): TrayLanguage {
  return String(locale ?? '').toLowerCase().startsWith('en') ? 'en' : 'zh'
}

export function trayStrings(locale?: string): TrayStrings {
  return TRAY_STRINGS[trayLanguage(locale)]
}

// ─── Dev-mode autostart launcher ───────────────────────────────────────────

export const DEV_AUTOSTART_FILENAME = 'plobi_desktop_autostart.vbs'

/**
 * Build the Windows Startup script that relaunches a *dev* desktop checkout.
 * Only used when the app isn't packaged: an installed build owns "launch at
 * login" through app.setLoginItemSettings, but a dev run has no installer to
 * register with, so we drop a hidden-window launcher in the user's Startup
 * folder — the same WScript.Shell pattern the existing chatlog / aigw
 * autostart scripts use. Window style 0 keeps the console hidden; the Electron
 * window is what the user is meant to see (and PLOBI_DESKTOP_START_HIDDEN
 * keeps even that in the tray on a cold boot).
 */
export function buildDevAutostartScript({
  workingDirectory,
  command,
  environment = {}
}: {
  workingDirectory: string
  command: string
  environment?: Record<string, string>
}): string {
  const lines = [
    "' Plobi desktop autostart (dev checkout) — written by WP-ENV-AUTOSTART.",
    "' Toggle it from the tray menu; deleting this file disables autostart.",
    'Option Explicit',
    'Dim sh, env',
    'Set sh = CreateObject("WScript.Shell")',
    'Set env = sh.Environment("PROCESS")'
  ]

  for (const [key, value] of Object.entries(environment)) {
    lines.push(`env.Item("${key}") = "${value}"`)
  }

  lines.push(`sh.CurrentDirectory = "${workingDirectory}"`, `sh.Run "${command}", 0, False`, '')

  return lines.join('\r\n')
}
