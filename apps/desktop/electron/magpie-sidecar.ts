/**
 * magpie ships with us — the vendor-adapter layer (MIT, github.com/yetone/magpie).
 *
 * Five rules this file keeps:
 *
 * 1. **The binary comes from the package or from our own verified download —
 *    never from whatever happens to be on PATH.**
 *    `scripts/fetch-magpie.mjs` puts the pinned build into `build/magpie/`
 *    before packaging (packaged: `resources/magpie/`, dev: `build/magpie/`), and
 *    that copy is the floor: it is what runs when a downloaded build is absent
 *    or was marked bad. A downloaded build in
 *    `<PLOBI_HOME>/magpie-runtime/bin/<version>/` wins over it, and only because
 *    `magpie-channel.ts` hashed it against upstream's `SHA256SUMS` first — and
 *    only when it is *newer* than the packaged copy, which names itself in
 *    `magpie/version.json`. A machine that already runs its own magpie
 *    must keep running *its* magpie, and we must never quietly attach to it:
 *    that instance is not ours to stop, its data is not ours to read, and its
 *    providers are not the ones we told the gateway about.
 * 2. **Its own port, not the default one.** `magpie serve` reads `port` from
 *    `settings.json`; 3425 is the port a hand-installed magpie uses, so we
 *    write a port of our own there (reused across boots when it is still free).
 *    Two magpies on one machine no longer fight over 3425, and a leftover
 *    listener from a previous run is detected instead of waited out.
 * 3. **Its runtime data stays on the data volume.** `XDG_CONFIG_HOME` is pinned
 *    to `<PLOBI_HOME>`, so magpie's `$XDG_CONFIG_HOME/magpie` — settings, logins,
 *    plugin cache — lands in `<PLOBI_HOME>/magpie` and never in
 *    `C:\Users\<user>\.config` (裁定 109 三: 运行数据禁残留 C 盘). The gateway
 *    gets the same environment (`magpieEnv`), so the side that *writes* and the
 *    side that *reads* the port agree on one directory.
 * 4. **What it says is not thrown away.** stdout/stderr go to
 *    `<PLOBI_HOME>/logs/magpie.log`, which is where a user (and we) can find
 *    out why it did not come up. `stdio: 'ignore'` used to swallow the exact
 *    sentence that explained it.
 * 5. **It does not phone home.** `DO_NOT_TRACK=1` — magpie reports usage to
 *    usemagpie.ai once a day otherwise, and our users never agreed to that.
 */
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { clearCurrent, markBad, resolveUpdatedBuild } from './magpie-channel'

/**
 * Where this module lives. The electron main is bundled as **ESM**
 * (`bundle-electron-main.mjs`: `format: 'esm'`), and ESM has no `__dirname` —
 * asking for it threw `__dirname is not defined` and took the whole gateway
 * (the quota service) down with it.
 */
const HERE = path.dirname(fileURLToPath(import.meta.url))

export interface MagpieSidecar {
  /** The binary we will run, or null when this package does not carry one. */
  binary: string | null
  /** Whether magpie is (or was already) answering on `port`. */
  started: boolean
  /** The port we pinned in its settings — null when there is no binary. */
  port: number | null
  /** The child, so the app can stop it on quit. */
  child?: ChildProcess
  /**
   * The pid we own on `port` — the child's, or one we adopted from a previous
   * boot (see `pidFile`). Windows has no job-object handle here, so this is how
   * a restart finds the process it must take back.
   */
  pid?: number
  /** Why it is not running, when `started` is false and a binary exists. */
  reason?: string
}

let running: MagpieSidecar | null = null

/** its data directory: `$XDG_CONFIG_HOME/magpie` with XDG pinned to PLOBI_HOME. */
export function magpieDataDir(plobiHome: string): string {
  return path.join(plobiHome, 'magpie')
}

/** Where its settings.json lives — the file that carries the port. */
export function magpieSettingsPath(plobiHome: string): string {
  return path.join(magpieDataDir(plobiHome), 'settings.json')
}

function logPath(plobiHome: string): string {
  return path.join(plobiHome, 'logs', 'magpie.log')
}

/** One log file, rotated once at 2 MB — same courtesy the other sidecars get. */
function logLine(plobiHome: string, line: string): void {
  try {
    const file = logPath(plobiHome)
    fs.mkdirSync(path.dirname(file), { recursive: true })
    try {
      if (fs.statSync(file).size > 2 * 1024 * 1024) fs.renameSync(file, `${file}.1`)
    } catch {
      // no file yet, or it cannot be rotated — appending still works
    }
    fs.appendFileSync(file, `${new Date().toISOString()} ${line}\n`)
  } catch {
    // logging must never be the reason the app fails
  }
}

interface SidecarRecord {
  pid: number
  port: number | null
}

/**
 * The sidecar's own fingerprint, next to the runtime bits Plobi already keeps.
 *
 * Windows has no job object here, and Electron can be killed (crash, Task
 * Manager, a machine that shuts down mid-build). The child and its node plugins
 * survive that, hold our data directory, and answer on our port — so the next
 * boot adopts them as "already serving" and a restart can never take the port
 * back. This file is what makes those leftovers findable again.
 */
function pidFile(plobiHome: string): string {
  return path.join(plobiHome, 'runtime', 'magpie-sidecar.json')
}

function readRecord(plobiHome: string): SidecarRecord | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(pidFile(plobiHome), 'utf8')) as SidecarRecord
    if (typeof parsed?.pid === 'number' && parsed.pid > 0) {
      return { pid: parsed.pid, port: typeof parsed.port === 'number' ? parsed.port : null }
    }
  } catch {
    // no file, or a half-written one from a hard kill
  }

  return null
}

function writeRecord(plobiHome: string, record: SidecarRecord): void {
  try {
    const file = pidFile(plobiHome)
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, JSON.stringify(record), 'utf8')
  } catch {
    // diagnostics only — never a reason to fail the boot
  }
}

function clearRecord(plobiHome: string): void {
  try {
    fs.rmSync(pidFile(plobiHome), { force: true })
  } catch {
    // already gone
  }
}

/** Whether `pid` is a live process on this machine. Signal 0 only probes. */
function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)

    return true
  } catch {
    return false
  }
}

function candidates(plobiHome?: string): string[] {
  const names = process.platform === 'win32' ? ['magpie-cli.exe', 'magpie-cli'] : ['magpie-cli']
  const out: string[] = []
  // A build we downloaded and verified outranks the one in the installer: that
  // is the whole point of `magpie-channel.ts` — the adapter layer moves without
  // a Plobi release. `readCurrent` already refuses anything not under our bin
  // root, not matching a version shape, or marked bad, so a stale pointer here
  // simply means "fall through to the packaged copy".
  if (plobiHome) {
    const updated = resolveUpdatedBuild(plobiHome)
    if (updated) out.push(updated.binary)
  }
  const roots = [
    // Packaged: electron-builder puts `build/magpie` under `resources/`.
    path.join(process.resourcesPath ?? '', 'magpie'),
    // Dev: the same build tree, before packaging.
    path.resolve(HERE, '..', 'build', 'magpie')
  ]
  for (const root of roots) {
    for (const name of names) {
      out.push(path.join(root, name))
    }
  }
  return out
}

/**
 * The copy that ships inside the package, or null.
 *
 * Deliberately no PATH fallback: borrowing `magpie-cli` from the user's own
 * install would start a gateway whose data directory, whose providers and whose
 * port belong to another application. Missing means missing — the gateway says
 * so on the card instead of pretending the vendor layer is here.
 */
export function resolveMagpieBinary(plobiHome?: string): string | null {
  for (const candidate of candidates(plobiHome)) {
    try {
      if (fs.existsSync(candidate)) return candidate
    } catch {
      // an unreadable path is not a reason to fail the boot
    }
  }
  return null
}

/**
 * The environment magpie runs with, used by BOTH the sidecar and the gateway.
 *
 * `XDG_CONFIG_HOME` is the whole point: magpie writes `$XDG_CONFIG_HOME/magpie`
 * and the gateway (quota_hub) reads the port from there, so pinning it here —
 * once, in one function — is what keeps the reader and the writer on one
 * directory instead of one on D: and one in the user's home.
 */
export function magpieEnv(plobiHome: string, port?: number | null): Record<string, string> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    XDG_CONFIG_HOME: plobiHome,
    DO_NOT_TRACK: '1'
  }
  if (port && Number.isInteger(port) && port > 0) env.PLOBI_MAGPIE_PORT = String(port)
  return env
}

/** Whether `port` on the loopback interface is free to bind. */
function portIsFree(port: number): Promise<boolean> {
  return new Promise(resolve => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => server.close(() => resolve(true)))
    server.listen(port, '127.0.0.1')
  })
}

/** Whether something on `port` says it is magpie (`GET /api/hello`). */
function magpieAnswersOn(port: number): Promise<boolean> {
  return new Promise(resolve => {
    const socket = net.connect({ port, host: '127.0.0.1' })
    const done = (ok: boolean) => {
      socket.removeAllListeners()
      socket.destroy()
      resolve(ok)
    }
    socket.setTimeout(1200)
    socket.once('error', () => done(false))
    socket.once('timeout', () => done(false))
    socket.once('connect', () => {
      socket.write('GET /api/hello HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
      let body = ''
      socket.on('data', chunk => {
        body += chunk.toString('utf8')
        if (body.includes('magpie')) done(true)
      })
      socket.once('close', () => done(body.includes('magpie')))
    })
  })
}

function readSettings(plobiHome: string): Record<string, unknown> {
  try {
    const raw = fs.readFileSync(magpieSettingsPath(plobiHome), 'utf8')
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

/** The port we used last boot, when it is still a sane number. */
function pinnedPort(plobiHome: string): number | null {
  const value = readSettings(plobiHome).port
  return typeof value === 'number' && Number.isInteger(value) && value > 0 && value < 65536 ? value : null
}

/** The port the gateway was told about, if this boot already chose one. */
function currentPort(): number | null {
  const value = Number.parseInt(process.env.PLOBI_MAGPIE_PORT ?? '', 10)
  return Number.isInteger(value) && value > 0 ? value : null
}

/** A free loopback port from the OS. */
function ephemeralPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => (port ? resolve(port) : reject(new Error('no port'))))
    })
  })
}

/** Write `port` into its settings.json, keeping every other key it owns. */
function pinPort(plobiHome: string, port: number): void {
  const settings = readSettings(plobiHome)
  if (settings.port === port) return
  settings.port = port
  const file = magpieSettingsPath(plobiHome)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
}

/**
 * A downloaded build that will not serve is worse than an old one that will.
 *
 * Mark the version bad, forget the pointer, and start again: the second call
 * sees no updated build and lands on the packaged copy (`candidates` falls
 * through). `markBad` is what stops the next boot from repeating this exact
 * failure, and it only ever names a *downloaded* version — the packaged copy is
 * the installer's own and may not be condemned here.
 *
 * Returns null when there was nothing to fall back from, so both failure sites
 * stay one line.
 */
async function fallBackFromUpdate(
  plobiHome: string,
  updated: { version: string } | null,
  reason: string
): Promise<MagpieSidecar | null> {
  if (!updated) return null
  markBad(plobiHome, updated.version, reason)
  clearCurrent(plobiHome)
  logLine(plobiHome, `magpie: ${updated.version} did not serve (${reason}) — back to the packaged copy`)
  // Take the port back before the packaged copy asks for it.
  stopMagpieSidecar(plobiHome)
  await new Promise(resolve => setTimeout(resolve, 900))
  running = null
  return ensureMagpieSidecar(plobiHome)
}

/**
 * Start magpie if it is not already answering, on a port of our own.
 *
 * The gateway reads magpie's provider list and its model catalogue, so magpie
 * has to be up before the gateway asks it anything. We start it here, once, and
 * hand the port to the gateway (`PLOBI_MAGPIE_PORT` env and `magpieEnv`), so both
 * processes talk to the same copy on the same port.
 */
export async function ensureMagpieSidecar(plobiHome: string): Promise<MagpieSidecar> {
  if (running) return running

  const binary = resolveMagpieBinary(plobiHome)
  if (!binary) {
    logLine(plobiHome, 'magpie: no binary in this package — the vendor layer stays off')
    running = { binary: null, started: false, port: null, reason: 'the package carries no magpie binary' }
    return running
  }

  // Which build are we about to run? Only a downloaded one can be a bad update
  // (the packaged copy is what the installer shipped, and marking *it* would
  // leave this machine with no vendor layer at all), so remember the version.
  const updated = resolveUpdatedBuild(plobiHome)

  fs.mkdirSync(magpieDataDir(plobiHome), { recursive: true })

  // 1. A port we used before, when it is still ours to take.
  let port = await (async () => {
    const pinned = pinnedPort(plobiHome)
    if (pinned && (await portIsFree(pinned))) return pinned
    if (pinned && (await magpieAnswersOn(pinned))) return pinned // our own leftover, still serving
    return ephemeralPort()
  })()

  // 2. Something already runs on the port we pinned and it still answers as
  //    magpie: reuse it instead of starting a second one that would spin.
  if (await magpieAnswersOn(port)) {
    // Our own leftover from a boot that ended badly (see `pidFile`): adopt its
    // pid, so `restartMagpieSidecar` can take the port back instead of leaving
    // a process it has no handle on.
    const record = readRecord(plobiHome)
    const adopted = record && record.port === port && pidAlive(record.pid) ? record.pid : undefined
    logLine(plobiHome, `magpie: already serving on ${port} — reusing it${adopted ? ` (pid ${adopted})` : ' (not ours to stop)'}`)
    if (adopted) writeRecord(plobiHome, { pid: adopted, port })
    process.env.PLOBI_MAGPIE_PORT = String(port)
    running = { binary, started: true, port, pid: adopted }
    return running
  }

  // 3. A fresh port, pinned where the gateway will read it.
  if (!(await portIsFree(port))) port = await ephemeralPort()
  try {
    pinPort(plobiHome, port)
  } catch (err) {
    logLine(plobiHome, `magpie: could not pin port ${port}: ${String(err)}`)
  }
  process.env.PLOBI_MAGPIE_PORT = String(port)
  process.env.XDG_CONFIG_HOME = plobiHome

  let child: ChildProcess
  try {
    child = spawn(binary, ['serve'], {
      cwd: path.dirname(binary),
      env: magpieEnv(plobiHome, port),
      detached: false,
      // keep the pipe: the sentence it prints when it cannot serve is the only
      // explanation the user will ever get.
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
  } catch (err) {
    logLine(plobiHome, `magpie: spawn failed: ${String(err)}`)
    const recovered = await fallBackFromUpdate(plobiHome, updated, `spawn failed: ${String(err)}`)
    if (recovered) return recovered
    running = { binary, started: false, port, reason: `spawn failed: ${String(err)}` }
    return running
  }

  let sawGateway = false
  const onOutput = (prefix: string) => (chunk: Buffer) => {
    const text = chunk.toString('utf8')
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue
      logLine(plobiHome, `${prefix} ${line.trimEnd()}`)
      if (/gateway on http:\/\/127\.0\.0\.1:/i.test(line)) sawGateway = true
      if (/another magpie/i.test(line)) {
        logLine(plobiHome, 'magpie: another instance owns this port — not serving')
      }
    }
  }
  child.stdout?.on('data', onOutput('stdout'))
  child.stderr?.on('data', onOutput('stderr'))
  child.on('error', err => logLine(plobiHome, `magpie: process error: ${String(err)}`))

  if (child.pid) writeRecord(plobiHome, { pid: child.pid, port })
  running = { binary, started: true, port, child, pid: child.pid }

  // 4. Give it a moment and tell the truth in the log: "started" without a
  //    listening gateway is how the last version lost an afternoon.
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    if (await magpieAnswersOn(port)) {
      logLine(plobiHome, `magpie: serving on ${port}`)
      return running
    }
    if (child.exitCode !== null) break
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  if (!sawGateway) {
    logLine(plobiHome, `magpie: did not answer on ${port} within 8s (see this log above)`)
  }
  // Not answering is a failure, whoever is to blame — but only a downloaded
  // build can be handed back. The packaged copy keeps the "did not answer"
  // answer, which is the honest one when there is nothing else to try.
  const recovered = await fallBackFromUpdate(plobiHome, updated, `no answer on ${port} within 8s`)
  if (recovered) return recovered
  return running
}

/** Where magpie keeps its own data, for the importer and for diagnostics. */
export function magpieRuntimeInfo(plobiHome: string): { dataDir: string; settingsPath: string; port: number | null } {
  return {
    dataDir: magpieDataDir(plobiHome),
    settingsPath: magpieSettingsPath(plobiHome),
    port: currentPort() ?? pinnedPort(plobiHome)
  }
}

/**
 * Stop and start again, so plugins and sign-ins imported a minute ago load.
 *
 * Importing configuration writes files magpie reads *once at boot*. Without a
 * restart the user finishes onboarding against a gateway that still has no
 * providers, which looks exactly like a failed import.
 */
export async function restartMagpieSidecar(plobiHome: string): Promise<MagpieSidecar> {
  const hadChild = Boolean(running?.child ?? running?.pid)

  stopMagpieSidecar(plobiHome)
  // Its port is released asynchronously; starting into a socket that is still
  // closing would make the new child lose the race and look like a conflict.
  if (hadChild) await new Promise(resolve => setTimeout(resolve, 900))
  running = null
  return ensureMagpieSidecar(plobiHome)
}

/**
 * Stop magpie when the app quits (the same courtesy the other sidecars get).
 *
 * Acts on the **pid**, not on the child handle, so it also stops a process we
 * adopted from a previous boot. Its plugins are Node processes of their own, so
 * Windows gets `taskkill /T` — a bare kill leaves them holding the data
 * directory open and the port bound.
 */
export function stopMagpieSidecar(plobiHome?: string): void {
  const pid = running?.child?.pid ?? running?.pid

  if (!pid) {
    running = null
    if (plobiHome) clearRecord(plobiHome)
    return
  }

  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
    } else {
      process.kill(pid, 'SIGTERM')
    }
  } catch {
    // already gone
  }

  running = null
  if (plobiHome) clearRecord(plobiHome)
}
