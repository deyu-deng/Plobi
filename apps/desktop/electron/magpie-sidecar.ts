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
import { type ChildProcess, execFile, spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { clearCurrent, listInstalledBuilds, markBad, normalizeMagpieVersion, packagedBuild, pointCurrentAt, resolveUpdatedBuild } from './magpie-channel'
import { seedMagpiePlugins } from './magpie-plugins'

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
  /**
   * The build that actually answers on `port`, read from `GET /api/hello`.
   *
   * Deliberately not "the version we meant to start". `started: true` used to
   * mean only "we spawned something and something answered", which is how a
   * build that had been downloaded, verified and pointed at could still never
   * have been executed while every log line said it was fine.
   */
  version?: string | null
}

let running: MagpieSidecar | null = null

/**
 * Whether a fallback is already in progress.
 *
 * A fallback starts a sidecar, and a sidecar that will not start calls back
 * into `fallBackFromUpdate`. Without this guard the second call stops the build
 * the first one just started and the two take turns forever.
 */
let fallingBack = false

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
      if (fs.statSync(file).size > 2 * 1024 * 1024) {fs.renameSync(file, `${file}.1`)}
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

/**
 * End `pid`, and everything it spawned.
 *
 * Windows gets `taskkill /T` because magpie's plugins are child processes of
 * their own: a bare kill leaves them holding the data directory open and the
 * port bound, which is exactly the state that made a restart land on a socket
 * that was still busy.
 */
function killPid(pid: number, plobiHome?: string): void {
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
    } else {
      process.kill(pid, 'SIGTERM')
    }
  } catch (err) {
    // already gone, or not ours to kill
    if (plobiHome) {logLine(plobiHome, `magpie: could not stop pid ${pid}: ${String(err)}`)}
  }
}

/**
 * The pid listening on `port`, asked of the OS rather than of our own record.
 *
 * `taskkill` is asynchronous and the record used to be cleared before it
 * landed, so a live process could end up with no handle on it at all — the
 * reason a downloaded build sat on disk while the previous one kept serving.
 */
function pidListeningOn(port: number): Promise<number | null> {
  if (process.platform !== 'win32') {return Promise.resolve(null)}

  return new Promise(resolve => {
    execFile('netstat', ['-ano', '-p', 'TCP'], { windowsHide: true, timeout: 5000 }, (err, stdout) => {
      if (err || !stdout) {
        resolve(null)

        return
      }

      for (const line of stdout.split(/\r?\n/)) {
        const parts = line.trim().split(/\s+/)

        if (parts.length < 5 || !/LISTEN/i.test(parts[3] ?? '')) {continue}

        const local = parts[1] ?? ''
        const localPort = Number.parseInt(local.slice(local.lastIndexOf(':') + 1), 10)
        const pid = Number.parseInt(parts[4] ?? '', 10)

        if (localPort === port && Number.isInteger(pid) && pid > 0) {
          resolve(pid)

          return
        }
      }

      resolve(null)
    })
  })
}

/** Wait for `pids` to stop existing. True when every one of them is gone. */
async function waitPidsGone(pids: number[], timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    if (pids.every(pid => !pidAlive(pid))) {return true}
    await new Promise(resolve => setTimeout(resolve, 150))
  }

  return pids.every(pid => !pidAlive(pid))
}

/** Wait for `port` to be bindable again. True when it is. */
async function waitPortFree(port: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    if (await portIsFree(port)) {return true}
    await new Promise(resolve => setTimeout(resolve, 150))
  }

  return portIsFree(port)
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
    if (updated) {out.push(updated.binary)}
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
      if (fs.existsSync(candidate)) {return candidate}
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
 * and the gateway (bezel) reads the port from there, so pinning it here —
 * once, in one function — is what keeps the reader and the writer on one
 * directory instead of one on D: and one in the user's home.
 */
export function magpieEnv(plobiHome: string, port?: number | null): Record<string, string> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    XDG_CONFIG_HOME: plobiHome,
    DO_NOT_TRACK: '1'
  }
  if (port && Number.isInteger(port) && port > 0) {env.PLOBI_MAGPIE_PORT = String(port)}
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

/**
 * What magpie says on `GET /api/hello`: does it answer, and as which build.
 *
 * The body is `{"name":"magpie","version":"0.1.4572"}` (measured 2026-11-20).
 * The version matters because "something answers on our port" and "the build we
 * just installed is the one answering" are different statements, and treating
 * the first as the second is how an entire update went missing.
 */
function magpieHello(port: number): Promise<{ answers: boolean; version: string | null }> {
  return new Promise(resolve => {
    const socket = net.connect({ port, host: '127.0.0.1' })
    let body = ''
    const done = (answers: boolean) => {
      socket.removeAllListeners()
      socket.destroy()
      resolve({ answers, version: helloVersion(body) })
    }
    socket.setTimeout(1200)
    socket.once('error', () => done(false))
    socket.once('timeout', () => done(false))
    socket.once('connect', () => {
      socket.write('GET /api/hello HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
      socket.on('data', chunk => {
        body += chunk.toString('utf8')
        if (body.includes('"version"')) {done(true)}
      })
      socket.once('close', () => done(body.includes('magpie')))
    })
  })
}

/** `0.1.4572` out of a hello body, normalised so a pointer's `v0.1.4572` matches. */
function helloVersion(body: string): string | null {
  const match = /"version"\s*:\s*"([^"]+)"/i.exec(body)

  return match?.[1] ? normalizeMagpieVersion(match[1]) : null
}

/** Whether something on `port` says it is magpie. */
async function magpieAnswersOn(port: number): Promise<boolean> {
  return (await magpieHello(port)).answers
}

/**
 * Take `port` back from whatever holds it, and wait until it is really free.
 *
 * `taskkill` returns when the request is accepted, not when the socket closes,
 * so starting the replacement right away makes the new child lose the bind and
 * report a conflict that was never real. Returns whether the port is free
 * afterwards, so the caller can pick another one instead of starting a build
 * that cannot listen.
 */
async function stopSidecarOnPort(plobiHome: string, port: number): Promise<boolean> {
  const pids = new Set<number>()

  if (running?.child?.pid) {pids.add(running.child.pid)}
  if (running?.pid) {pids.add(running.pid)}

  const record = readRecord(plobiHome)
  if (record && record.pid > 0 && pidAlive(record.pid)) {pids.add(record.pid)}

  const owner = await pidListeningOn(port)
  if (owner) {pids.add(owner)}

  running = null

  if (!pids.size) {
    logLine(plobiHome, `magpie: ${port} answers but no pid could be found for it — leaving it alone`)

    return false
  }

  for (const pid of pids) {killPid(pid, plobiHome)}

  if (!(await waitPidsGone([...pids], 8000))) {
    logLine(plobiHome, `magpie: pid(s) ${[...pids].join(',')} still alive 8s after the stop`)
  }

  const free = await waitPortFree(port, 8000)
  if (!free) {logLine(plobiHome, `magpie: port ${port} is still bound 8s after the stop`)}

  return free
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
  if (settings.port === port) {return}
  settings.port = port
  const file = magpieSettingsPath(plobiHome)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
}

/**
 * A downloaded build that will not serve is worse than an old one that will.
 *
 * Mark the version bad, forget the pointer, and step back to the newest build
 * still on disk. The packaged copy is the *last* resort, not the first: the
 * installer carries v0.1.3403 while a verified v0.1.4371 may have been serving
 * for days, so falling straight back to the package is a hundred-build
 * downgrade dressed up as a recovery (2026-11-20).
 *
 * Returns null when there was nothing to fall back from, so both failure sites
 * stay one line.
 */
async function fallBackFromUpdate(
  plobiHome: string,
  updated: { version: string } | null,
  reason: string
): Promise<MagpieSidecar | null> {
  if (!updated) {return null}

  // A fallback starts a sidecar, and a sidecar that will not start calls back
  // in here. Without this guard the second call stops the build the first one
  // just started and the two take turns forever.
  if (fallingBack) {return null}
  fallingBack = true

  try {
    markBad(plobiHome, updated.version, reason)
    clearCurrent(plobiHome)
    logLine(plobiHome, `magpie: ${updated.version} did not serve (${reason})`)

    // Take the port back, and wait until it is really back — `taskkill` is not
    // synchronous and the replacement needs the bind.
    await stopMagpieSidecarAndWait(plobiHome)
    running = null

    // `markBad` above already keeps the failed version out of this list.
    for (const build of listInstalledBuilds(plobiHome)) {
      const pointed = await pointCurrentAt(plobiHome, build)
      if (!pointed) {continue} // below the packaged floor: not a rollback, a downgrade
      logLine(plobiHome, `magpie: rolling back to ${build.version}`)
      const sidecar = await ensureMagpieSidecar(plobiHome)
      if (sidecar.started) {return sidecar}
      markBad(plobiHome, build.version, 'rolled back to it and it did not serve either')
      clearCurrent(plobiHome)
      await stopMagpieSidecarAndWait(plobiHome)
    }

    logLine(plobiHome, 'magpie: no usable downloaded build left — starting the packaged copy')
    return ensureMagpieSidecar(plobiHome)
  } finally {
    fallingBack = false
  }
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
  if (running) {return running}

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

  // What this boot must end up running: the downloaded build the pointer names,
  // or the copy the installer carried. Kept apart from `binary` because a
  // process already holding the port may be running something else entirely,
  // and "it answers" is not the same claim as "it answers as this build".
  const expectedVersion = normalizeMagpieVersion(updated?.version ?? packagedBuild()?.version ?? null)

  fs.mkdirSync(magpieDataDir(plobiHome), { recursive: true })

  // The plugin set, before magpie boots: a machine with no plugins has no
  // providers at all (measured 2026-11-10 — the host publishes an empty
  // roster), and every subscription card then reads "not installed". The rule
  // is one line and it is the same rule the binary follows: **seed only into a
  // data directory that has no plugin set, never over one that does.** A magpie
  // that was updated on its own keeps the plugins it manages.
  const runningVersion = updated?.version || packagedBuild()?.version || ''
  try {
    const seeded = seedMagpiePlugins(plobiHome, { runningMagpieVersion: runningVersion })
    if (seeded.seeded) {
      logLine(plobiHome, `magpie: ${seeded.reason}`)
    }
  } catch (err) {
    // A missing plugin set is a degraded vendor layer, not a reason to refuse
    // to start: magpie still boots, and the user can install the plugins from
    // its own marketplace.
    logLine(plobiHome, `magpie: could not seed the plugin set: ${String(err)}`)
  }

  // 1. A port we used before, when it is still ours to take.
  let port = await (async () => {
    const pinned = pinnedPort(plobiHome)
    if (pinned && (await portIsFree(pinned))) {return pinned}
    if (pinned && (await magpieAnswersOn(pinned))) {return pinned} // our own leftover, still serving
    return ephemeralPort()
  })()

  // 2. Something already answers on the port we pinned. Reusing it is only
  //    honest when it answers as *the build this boot must run* — asking "does
  //    anything answer" and stopping there is what swallowed a verified update
  //    on 2026-11-20: the previous build still held the port, the new binary was
  //    never executed, and the pointer said the update had landed.
  const reuse = await magpieHello(port)
  if (reuse.answers) {
    const served = reuse.version
    const sameBuild = !served || !expectedVersion || served === expectedVersion

    if (!sameBuild) {
      logLine(
        plobiHome,
        `magpie: ${port} is served by ${served}, but this boot must run ${expectedVersion} — taking the port back`
      )
      const freed = await stopSidecarOnPort(plobiHome, port)
      // It answered and could not be stopped: take another port rather than
      // start a build that will lose the bind and look like a bad update.
      if (!freed) {port = await ephemeralPort()}
    } else {
      // Our own leftover from a boot that ended badly (see `pidFile`): adopt its
      // pid, so `restartMagpieSidecar` can take the port back instead of leaving
      // a process it has no handle on. When the record lost the pid — it used to
      // be cleared before the process was actually gone — ask the OS who owns
      // the port instead of declaring it "not ours to stop".
      const record = readRecord(plobiHome)
      const recorded = record && record.port === port && record.pid > 0 && pidAlive(record.pid) ? record.pid : undefined
      const owner = recorded ?? (await pidListeningOn(port)) ?? undefined
      logLine(
        plobiHome,
        `magpie: already serving on ${port} as ${served ?? 'version unknown'}${owner ? ` (pid ${owner})` : ' (pid unknown)'} — reusing it`
      )
      if (owner) {writeRecord(plobiHome, { pid: owner, port })}
      process.env.PLOBI_MAGPIE_PORT = String(port)
      running = { binary, started: true, port, pid: owner, version: served ?? expectedVersion }
      return running
    }
  }

  // 3. A fresh port, pinned where the gateway will read it.
  if (!(await portIsFree(port))) {port = await ephemeralPort()}
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
    if (recovered) {return recovered}
    running = { binary, started: false, port, reason: `spawn failed: ${String(err)}` }
    return running
  }

  let sawGateway = false
  const onOutput = (prefix: string) => (chunk: Buffer) => {
    const text = chunk.toString('utf8')
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) {continue}
      logLine(plobiHome, `${prefix} ${line.trimEnd()}`)
      if (/gateway on http:\/\/127\.0\.0\.1:/i.test(line)) {sawGateway = true}
      if (/another magpie/i.test(line)) {
        logLine(plobiHome, 'magpie: another instance owns this port — not serving')
      }
    }
  }
  child.stdout?.on('data', onOutput('stdout'))
  child.stderr?.on('data', onOutput('stderr'))
  child.on('error', err => logLine(plobiHome, `magpie: process error: ${String(err)}`))

  if (child.pid) {writeRecord(plobiHome, { pid: child.pid, port })}
  running = { binary, started: true, port, child, pid: child.pid }

  // 4. Give it a moment and tell the truth in the log: "started" without a
  //    listening gateway is how the last version lost an afternoon.
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    const hello = await magpieHello(port)
    if (hello.answers) {
      // Record what answered, not what we spawned: this is the value the update
      // ledger shows the user, and it is the only one that cannot lie about
      // which build is actually serving.
      running = {
        binary,
        started: true,
        port,
        child,
        pid: child.pid,
        version: hello.version ?? expectedVersion
      }
      logLine(plobiHome, `magpie: serving on ${port} as ${running.version ?? 'version unknown'}`)
      return running
    }
    if (child.exitCode !== null) {break}
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  if (!sawGateway) {
    logLine(plobiHome, `magpie: did not answer on ${port} within 8s (see this log above)`)
  }
  // Not answering is a failure, whoever is to blame — but only a downloaded
  // build can be handed back. The packaged copy keeps the "did not answer"
  // answer, which is the honest one when there is nothing else to try.
  const recovered = await fallBackFromUpdate(plobiHome, updated, `no answer on ${port} within 8s`)
  if (recovered) {return recovered}
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
  // Waiting for the *port*, not for a fixed 900 ms. `taskkill` is asynchronous:
  // starting the replacement into a socket that is still closing made the new
  // child lose the bind and report a conflict that was never there — the new
  // build then "started" and never served (2026-11-20).
  await stopMagpieSidecarAndWait(plobiHome)
  running = null
  return ensureMagpieSidecar(plobiHome)
}

/** Every pid we have a claim on: the child, the one we adopted, the recorded one. */
function stopCandidates(plobiHome?: string): number[] {
  const pids = new Set<number>()

  if (running?.child?.pid) {pids.add(running.child.pid)}
  if (running?.pid) {pids.add(running.pid)}

  if (plobiHome) {
    const record = readRecord(plobiHome)
    if (record && record.pid > 0) {pids.add(record.pid)}
  }

  return [...pids]
}

/**
 * Stop magpie when the app quits (the same courtesy the other sidecars get).
 *
 * Acts on the **pid**, not on the child handle, so it also stops a process we
 * adopted from a previous boot. Its plugins are Node processes of their own, so
 * Windows gets `taskkill /T` — a bare kill leaves them holding the data
 * directory open and the port bound.
 *
 * The record is only forgotten once the process is *confirmed* gone. Clearing it
 * first is how a live pid turned into `pid: undefined`, and how a port ended up
 * with an owner that nothing could name (2026-11-20).
 */
export function stopMagpieSidecar(plobiHome?: string): void {
  const pids = stopCandidates(plobiHome)

  for (const pid of pids) {killPid(pid, plobiHome)}
  running = null

  if (!plobiHome) {return}
  if (pids.every(pid => !pidAlive(pid))) {clearRecord(plobiHome)}
}

/**
 * The same stop, but it does not return until the port is really free.
 *
 * Used wherever something is about to *start* on that port (a restart, a
 * rollback): "I asked taskkill" and "the socket is closed" are different
 * moments, and only the second one lets a new magpie bind.
 */
export async function stopMagpieSidecarAndWait(plobiHome: string): Promise<void> {
  const pids = new Set<number>(stopCandidates(plobiHome))

  const port = currentPort() ?? pinnedPort(plobiHome)
  if (port) {
    const owner = await pidListeningOn(port)
    if (owner) {pids.add(owner)}
  }

  running = null

  for (const pid of pids) {killPid(pid, plobiHome)}

  const gone = await waitPidsGone([...pids], 8000)
  if (!gone) {logLine(plobiHome, `magpie: pid(s) ${[...pids].join(',')} survived taskkill`)}

  if (port && !(await waitPortFree(port, 8000))) {
    logLine(plobiHome, `magpie: port ${port} is still bound 8s after the stop`)
  }

  if (gone) {clearRecord(plobiHome)}
}
