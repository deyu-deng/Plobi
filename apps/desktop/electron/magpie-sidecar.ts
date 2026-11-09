/**
 * magpie ships with us — the vendor-adapter layer (MIT, github.com/yetone/magpie).
 *
 * Three rules this file keeps:
 *
 * 1. **The binary comes from the package, never from the internet at install
 *    time.** `scripts/fetch-magpie.mjs` puts the pinned build into
 *    `build/magpie/` before packaging; at runtime we only look for that copy
 *    (packaged: `resources/magpie/`, dev: `build/magpie/`) or one already on
 *    PATH. We never download at boot.
 * 2. **Its runtime data stays on the data volume.** `XDG_CONFIG_HOME` is pinned
 *    to `<PLOBI_HOME>/magpie`, so its settings, logins and plugin cache never
 *    land in `C:\Users\<user>\.config` (裁定 109 三: 运行数据禁残留 C 盘).
 * 3. **It does not phone home.** `DO_NOT_TRACK=1` — magpie reports usage to
 *    usemagpie.ai once a day otherwise, and our users never agreed to that.
 */
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Where this module lives — the bundle is ESM, where `__dirname` does not exist. */
const HERE = path.dirname(fileURLToPath(import.meta.url))

export interface MagpieSidecar {
  /** The binary we will run, or null when this machine has none. */
  binary: string | null
  /** Whether we started one. */
  started: boolean
  /** The child, so the app can stop it on quit. */
  child?: ChildProcess
}

let running: MagpieSidecar | null = null

function candidates(): string[] {
  const names = process.platform === 'win32' ? ['magpie-cli.exe', 'magpie-cli'] : ['magpie-cli']
  const roots = [
    // Packaged: electron-builder puts `build/magpie` under `resources/`.
    path.join(process.resourcesPath ?? '', 'magpie'),
    // Dev: the same build tree, before packaging.
    path.resolve(HERE, '..', 'build', 'magpie')
  ]
  const out: string[] = []
  for (const root of roots) {
    for (const name of names) {
      out.push(path.join(root, name))
    }
  }
  return out
}

/** Where magpie's CLI is on this machine, or null when it is not here. */
export function resolveMagpieBinary(): string | null {
  for (const candidate of candidates()) {
    try {
      if (fs.existsSync(candidate)) return candidate
    } catch {
      // an unreadable path is not a reason to fail the boot
    }
  }
  const pathDirs = (process.env.PATH ?? '').split(path.delimiter).filter(Boolean)
  for (const dir of pathDirs) {
    for (const name of process.platform === 'win32' ? ['magpie-cli.exe', 'magpie-cli'] : ['magpie-cli']) {
      const candidate = path.join(dir, name)
      try {
        if (fs.existsSync(candidate)) return candidate
      } catch {
        // keep looking
      }
    }
  }
  return null
}

/** The environment magpie runs with: its data on the data volume, no telemetry. */
export function magpieEnv(plobiHome: string): Record<string, string> {
  return {
    ...process.env,
    XDG_CONFIG_HOME: path.join(plobiHome, 'magpie'),
    DO_NOT_TRACK: '1'
  } as Record<string, string>
}

/**
 * Start magpie if it is not already answering.
 *
 * The gateway reads magpie's provider list and its model catalogue, so magpie
 * has to be up before the gateway asks it anything. We start it here, once,
 * and hand the path to the gateway (`PLOBI_MAGPIE_BINARY`) so both processes
 * talk to the same copy.
 */
export function ensureMagpieSidecar(plobiHome: string): MagpieSidecar {
  if (running) return running

  const binary = resolveMagpieBinary()
  if (!binary) {
    running = { binary: null, started: false }
    return running
  }

  try {
    fs.mkdirSync(path.join(plobiHome, 'magpie'), { recursive: true })
  } catch {
    // the gateway will report the real problem if the directory cannot be made
  }

  const child = spawn(binary, ['serve'], {
    cwd: path.dirname(binary),
    env: magpieEnv(plobiHome),
    detached: false,
    stdio: 'ignore',
    windowsHide: true
  })
  child.on('error', () => undefined)
  running = { binary, started: true, child }
  return running
}

/** Stop magpie when the app quits (the same courtesy the other sidecars get). */
export function stopMagpieSidecar(): void {
  if (!running?.child) return
  try {
    running.child.kill()
  } catch {
    // already gone
  }
  running = null
}
