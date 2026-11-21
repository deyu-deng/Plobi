import { spawn } from 'node:child_process'
/**
 * magpie moves on its own schedule; Plobi must not ship a release for it.
 *
 * The vendor-adapter layer in `magpie-sidecar.ts` used to be frozen at whatever
 * build the installer carried — v0.1.3403 was pinned on 2026-11-09 and by the
 * next morning upstream was at v0.1.3490. Adapters (providers, quota shapes)
 * belong to that binary, so a user whose quota view is wrong has to wait for a
 * whole Plobi installer today. This module is the way out: a second copy of the
 * binary, in a writable place, picked up ahead of the packaged one.
 *
 * Five rules this file keeps:
 *
 * 1. **The packaged copy is the floor, never a casualty.** An update lands in
 *    `<PLOBI_HOME>/magpie-runtime/bin/<version>/` — beside `magpie/`, never
 *    inside it: `magpie/` *is* magpie's XDG directory, so a binary in there is
 *    a 74 MB executable that a backup or a "copy my settings over" would drag
 *    along; `resources/magpie/` is left alone, so
 *    a downloaded build that cannot start is one pointer file away from being
 *    ignored (开箱即用 + 数据隔离 两条底线).
 * 2. **Nothing is installed that we did not hash.** Upstream publishes
 *    `SHA256SUMS` per release; we read the digest from *there*, download the
 *    binary, hash the bytes we actually got, and refuse on any mismatch. A
 *    truncated 74 MB download and a swapped asset both die here.
 * 3. **The download path is the API, not `github.com/releases/download`.**
 *    Measured on a real user machine: `api.github.com` answers in 2 s, the
 *    `/releases/download/` URL times out at 12 s, and
 *    `release-assets.githubusercontent.com` (where the API redirects to) is
 *    reachable. So we ask the API for the asset id and follow *its* redirect —
 *    the one URL pairing that works without a mirror.
 * 4. **Install is atomic and idempotent.** Bytes land in a `.part` file, get
 *    hashed, then rename into place; `current.json` is written the same way
 *    (temp + rename). A crash leaves either the old pointer or the new one,
 *    never a half-written binary that the sidecar would try to exec.
 * 5. **A build that failed to start is remembered.** `markBad()` drops a marker
 *    next to it, `resolveUpdatedBuild()` skips it, and the sidecar falls back to
 *    the packaged copy — no update loop, no silent downgrade of the whole app.
 * 6. **The floor is a real version, not a hope.** `fetch-magpie.mjs` writes
 *    `version.json` beside the packaged binary, so the copy this installer
 *    brought is *named*. A build downloaded last month cannot outrank it
 *    (`resolveUpdatedBuild` compares, `installBuild` refuses to go below), and
 *    the downgrade a swapped release channel would otherwise pull off dies in
 *    that same comparison.
 * 7. **Only the last usable builds stay.** Each version is ~74 MB on disk; once
 *    a newer one is verified we keep that and one step back for a rollback and
 *    drop the rest (`pruneOldBuilds`) — a quota adapter is not a version museum.
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Where this module lives. The electron main is bundled as **ESM**, so there is
 * no `__dirname` to lean on (same reason `magpie-sidecar.ts` computes it).
 */
const HERE = path.dirname(fileURLToPath(import.meta.url))

/** The vendor repo. Also the only place a version literal comes from. */
export const MAGPIE_UPSTREAM_REPO = 'yetone/magpie'

/** `v0.1.3490` — the shape upstream tags use; anything else is not a version. */
const VERSION_RE = /^v\d+\.\d+\.\d+$/
const SHA256_RE = /^[0-9a-f]{64}$/

/** A build we could run, and the digest that got it here. */
export interface MagpieBuild {
  version: string
  /** Absolute path of the executable. */
  binary: string
  sha256: string
}

/** What `current.json` records — a build plus when it became current. */
export interface MagpieCurrent extends MagpieBuild {
  installedAt: string
  /** Byte length written with the pointer; absent on pointers from before it. */
  size?: number
}

/** An upstream release, resolved to the one asset this machine needs. */
export interface MagpieRelease {
  version: string
  /** Where the bytes come from (an API endpoint that redirects, see rule 3). */
  url: string
  sha256: string
  size: number
  publishedAt: string
}

/**
 * `<PLOBI_HOME>/magpie-runtime/bin` — the writable home of downloaded builds.
 *
 * Not `<PLOBI_HOME>/magpie/bin`: that directory is magpie's own
 * `$XDG_CONFIG_HOME/magpie`, i.e. the thing a settings backup copies and the
 * thing a user pokes at by hand. Binaries live in a sibling so the config volume
 * holds config and nothing else.
 */
export function magpieBinRoot(plobiHome: string): string {
  return path.join(plobiHome, 'magpie-runtime', 'bin')
}

/**
 * Where a version's bytes wait between `curl` runs: `<bin>/<version>.part`.
 *
 * The name carries the version and nothing else. It used to end in
 * `.<pid>.part`, which made the file unique per *process* — and therefore
 * unreachable by the next one, so `-C -` resumed from zero every time and each
 * killed run left a 74 MB tombstone that no code path removed. One stable name
 * per version means the retry that matters, the one after this process is gone,
 * can find its own bytes.
 *
 * Two downloads of the same version would now share a file. They do not: the
 * check/install door in `main.ts` is a single button, and the worst case if one
 * ever raced is a digest mismatch, which `downloadVerified` answers by starting
 * over from zero.
 */
export function magpiePartPath(plobiHome: string, version: string): string {
  return path.join(magpieBinRoot(plobiHome), `${version}.part`)
}

/** `v0.1.3771.part`, or the pre-2026-11-13 `v0.1.3771.34360.part` (that number is a pid). */
const PART_RE = /^(v\d+\.\d+\.\d+)(?:\.(\d+))?\.part$/

/**
 * Remove part files no future download can use; return the names removed.
 *
 * Two shapes are dead on arrival:
 *  - the pid-suffixed names written before the naming fix — the pid that wrote
 *    them is gone, so nothing will ever resume them;
 *  - the bytes of a version that is already installed — the rename happened and
 *    only the copy was left behind.
 *
 * A plain `<version>.part` with no finished binary beside it is left in peace:
 * that is either an interrupted download worth resuming or the file the current
 * install is writing. Deleting those is how a resumable updater stops being
 * resumable. Pass `keepVersion` when the caller knows which version it is about
 * to install — then every *other* version's part is garbage too, and the sweep
 * keeps the pile from growing one 74 MB fossil per abandoned update.
 */
export function sweepStaleParts(
  plobiHome: string,
  platform: NodeJS.Platform = process.platform,
  keepVersion: string | null = null
): string[] {
  const root = magpieBinRoot(plobiHome)
  let entries: fs.Dirent[]

  try {
    entries = fs.readdirSync(root, { withFileTypes: true })
  } catch {
    return []
  }

  const exe = magpieExeName(platform)
  const removed: string[] = []

  for (const entry of entries) {
    if (!entry.isFile()) {continue}

    const match = PART_RE.exec(entry.name)

    if (!match) {continue}

    const [, version, pid] = match
    const stale = keepVersion !== null && version !== keepVersion

    if (!pid && !stale && !fs.existsSync(path.join(root, version, exe))) {continue}

    try {
      fs.rmSync(path.join(root, entry.name), { force: true })
      removed.push(entry.name)
    } catch {
      // a running virus scanner holding the handle; the next install tries again
    }
  }

  return removed
}

/** `v0.1.3492` > `v0.1.3403`. Anything unversioned compares equal to anything. */
export function compareMagpieVersion(a: string, b: string): number {
  if (!VERSION_RE.test(a) || !VERSION_RE.test(b)) {return 0}

  const left = a.slice(1).split('.').map((part) => Number.parseInt(part, 10))
  const right = b.slice(1).split('.').map((part) => Number.parseInt(part, 10))

  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) {return left[index] > right[index] ? 1 : -1}
  }

  return 0
}

/** The build that shipped inside this app, and where it sits. */
export interface MagpiePackagedBuild {
  version: string
  dir: string
  binary: string
  sha256: string | null
}

/**
 * Where the packaged copy lives: `resources/magpie` after packaging,
 * `build/magpie` in a dev tree — the same directory `fetch-magpie.mjs` fills.
 *
 * `process.resourcesPath` also exists under plain `electron .`, pointing at
 * electron's own resources, so the packaged path only wins when it actually
 * holds a `version.json`.
 */
export function packagedMagpieDir(): string {
  const resources = process.resourcesPath ?? ''
  const packaged = resources ? path.join(resources, 'magpie') : ''

  if (packaged && fs.existsSync(path.join(packaged, 'version.json'))) {return packaged}

  return path.resolve(HERE, '..', 'build', 'magpie')
}

/**
 * Read `<dir>/version.json` — written by `fetch-magpie.mjs` at build time.
 *
 * Without it the packaged copy has no name and we cannot tell a fresh installer
 * from an old one, which is exactly how a downloaded build ends up beating the
 * copy the user just updated to. Installers built before this file existed have
 * no `version.json`; that case returns null and callers keep the old behaviour.
 */
export function readPackagedBuild(
  dir: string,
  platform: NodeJS.Platform = process.platform
): MagpiePackagedBuild | null {
  let parsed: unknown

  try {
    parsed = JSON.parse(fs.readFileSync(path.join(dir, 'version.json'), 'utf8'))
  } catch {
    return null
  }

  if (!parsed || typeof parsed !== 'object') {return null}
  const value = parsed as Record<string, unknown>

  if (typeof value.version !== 'string' || !VERSION_RE.test(value.version)) {return null}

  const sha256 = typeof value.sha256 === 'string' && SHA256_RE.test(value.sha256) ? value.sha256 : null

  return {
    version: value.version,
    dir,
    binary: path.join(dir, magpieExeName(platform)),
    sha256
  }
}

/** The packaged copy as this process sees it (see `readPackagedBuild`). */
export function packagedBuild(platform: NodeJS.Platform = process.platform): MagpiePackagedBuild | null {
  return readPackagedBuild(packagedMagpieDir(), platform)
}

/** The version the installer pinned — the floor no download may go under. */
export function packagedFloorVersion(platform: NodeJS.Platform = process.platform): string | null {
  return packagedBuild(platform)?.version ?? null
}

/** The executable name we settle on, so the sidecar looks for one name only. */
export function magpieExeName(platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32' ? 'magpie-cli.exe' : 'magpie-cli'
}

/** `<os>-<arch>` as `third_party/magpie.json` and upstream both spell it. */
export function magpieTargetKey(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch
): string | null {
  const os = platform === 'win32' ? 'windows' : platform === 'darwin' ? 'darwin' : platform === 'linux' ? 'linux' : null
  const cpu = arch === 'x64' ? 'x64' : arch === 'arm64' ? 'arm64' : null

  return os && cpu ? `${os}-${cpu}` : null
}

/**
 * The upstream asset for this machine — `magpie-cli-windows-amd64.exe` and the
 * five siblings. Note `cli`: the bare `magpie-*` assets are the desktop app.
 */
export function magpieAssetName(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch
): string | null {
  const key = magpieTargetKey(platform, arch)

  if (!key) {return null}
  const [os, cpu] = key.split('-')
  const suffix = platform === 'win32' ? '.exe' : ''

  return `magpie-cli-${os}-${cpu === 'x64' ? 'amd64' : cpu}${suffix}`
}

function currentPointer(plobiHome: string): string {
  return path.join(magpieBinRoot(plobiHome), 'current.json')
}

function badMarker(plobiHome: string, version: string): string {
  return path.join(magpieBinRoot(plobiHome), version, '.bad')
}

/**
 * The build `current.json` points at, if it still describes something runnable.
 *
 * Every field is re-checked rather than trusted: the pointer is a file on a
 * user's disk, and a hand-edited `../../..` would otherwise turn into a path we
 * would happily exec. A build marked bad is reported as absent, which is what
 * makes the packaged copy win again.
 */
export function readCurrent(plobiHome: string): MagpieCurrent | null {
  let parsed: unknown

  try {
    parsed = JSON.parse(fs.readFileSync(currentPointer(plobiHome), 'utf8'))
  } catch {
    return null
  }

  if (!parsed || typeof parsed !== 'object') {return null}
  const value = parsed as Record<string, unknown>
  const { version, binary, sha256 } = value
  const installedAt = typeof value.installedAt === 'string' ? value.installedAt : ''
  const size = typeof value.size === 'number' && Number.isFinite(value.size) && value.size > 0 ? value.size : 0

  if (typeof version !== 'string' || !VERSION_RE.test(version)) {return null}

  if (typeof binary !== 'string' || !path.isAbsolute(binary)) {return null}

  if (!binary.startsWith(magpieBinRoot(plobiHome) + path.sep)) {return null}

  if (typeof sha256 !== 'string' || !SHA256_RE.test(sha256)) {return null}

  if (fs.existsSync(badMarker(plobiHome, version))) {return null}

  try {
    const stat = fs.statSync(binary)

    if (!stat.isFile()) {return null}
    // A truncated download or a swapped binary is not this build, whatever the
    // pointer claims — the size is the cheap half of the check, the hash the
    // expensive half we run when we actually install.
    if (size > 0 && stat.size !== size) {return null}
  } catch {
    return null
  }

  return { version, binary, sha256, size, installedAt }
}

/**
 * The downloaded build the sidecar should prefer over the packaged one.
 *
 * Only when it is genuinely *newer* than the copy this installer carries. The
 * pointer deliberately survives an app update — that is the whole point of it —
 * so without this comparison a build pulled two weeks ago would keep running
 * over the fresh one the user just installed, and the update would look like it
 * did nothing. A package without `version.json` (built before this file was
 * written) means "cannot tell", and the pointer keeps winning as it always did.
 */
export function resolveUpdatedBuild(
  plobiHome: string,
  platform: NodeJS.Platform = process.platform
): MagpieBuild | null {
  const current = readCurrent(plobiHome)

  if (!current) {return null}

  const floor = packagedBuild(platform)

  if (floor && compareMagpieVersion(current.version, floor.version) <= 0) {return null}

  return { version: current.version, binary: current.binary, sha256: current.sha256 }
}

/**
 * Record that `version` did not come up, and step back to the packaged copy.
 *
 * Called by the sidecar when the process dies before answering (see
 * `ensureMagpieSidecar`). The marker is what keeps the next boot from picking
 * the same build and failing the same way; the version directory itself stays
 * on disk for us to look at.
 */
export function markBad(plobiHome: string, version: string, reason: string): void {
  if (!VERSION_RE.test(version)) {return}

  try {
    const dir = path.join(magpieBinRoot(plobiHome), version)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(badMarker(plobiHome, version), `${new Date().toISOString()} ${reason}\n`, 'utf8')
  } catch {
    // a marker we cannot write is not a reason to fail the boot
  }
}

/** Forget the pointer (the packaged copy takes over). */
export function clearCurrent(plobiHome: string): void {
  try {
    fs.rmSync(currentPointer(plobiHome), { force: true })
  } catch {
    // same: best effort
  }
}

/** Streaming sha256 of a file we already have on disk. */
export function sha256File(file: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256')
    const stream = fs.createReadStream(file)
    stream.on('error', reject)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
  })
}

/**
 * Run curl. Every supported target has it (Windows 10+ ships `curl.exe`), and it
 * brings proxy handling, redirects and resume that would otherwise be a
 * hand-rolled HTTP client against a 74 MB file.
 *
 * `--noproxy '*'` is the fallback the release updater learned the hard way: a
 * broken local proxy config turns a working download into "spins forever", and
 * a direct connection is the recovery.
 */
function curl(args: string[], timeoutMs: number): Promise<{ stdout: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn('curl', args, { windowsHide: true })
    let out = ''
    let err = ''

    const timer = setTimeout(() => {
      child.kill()
      reject(new Error(`curl timed out after ${timeoutMs} ms`))
    }, timeoutMs)

    child.stdout?.on('data', (chunk) => {
      out += String(chunk)
    })
    child.stderr?.on('data', (chunk) => {
      err += String(chunk)
    })
    child.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      clearTimeout(timer)

      if (code === 0) {resolve({ stdout: out })}
      else {reject(new Error(`curl exited ${code}: ${err.trim().slice(0, 400)}`))}
    })
  })
}

/**
 * Fetch a small text body, trying the configured proxy first and direct second.
 *
 * `accept` is not cosmetic: `api.github.com/repos/.../releases/assets/<id>` is
 * two endpoints in one URL. Ask for `application/vnd.github+json` and you get
 * the asset's *metadata* (correct for release lookup, useless for a digest);
 * ask for `application/octet-stream` and it redirects to the real file.
 */
async function fetchText(
  url: string,
  accept = 'application/vnd.github+json',
  timeoutMs = 20_000
): Promise<string> {
  const args = [
    '-sS',
    '-L',
    '--fail',
    '--max-time',
    String(Math.ceil(timeoutMs / 1000)),
    '-H',
    `Accept: ${accept}`,
    url
  ]

  try {
    return (await curl(args, timeoutMs)).stdout
  } catch {
    return (await curl([...args.slice(0, -1), '--noproxy', '*', url], timeoutMs)).stdout
  }
}

/** Download `url` to `dest`, proxy first then direct, `Accept` tuned for assets.
 *
 * Two things this had to learn on 2026-11-11, from a real failed self-update on the
 * installed 0.3.1: `~/Plobi/magpie-runtime/bin/` held `v0.1.3754.95795.part` at 27 MB
 * and `v0.1.3754.99039.part` at 5 MB out of 74 MB, both written at the same minute,
 * and **not one line** in any log said so. So:
 *
 * 1. **A stall must fail, not hang.** `--max-time` alone let a quiet CDN connection
 *    eat the whole 30-minute budget; `--speed-limit/--speed-time` turns "no bytes
 *    for a minute" into a real exit code we can see and retry.
 * 2. **Resume instead of restart.** `-C -` picks up whatever is already in `dest`,
 *    so a retry costs the tail of the file, not all 74 MB again. That only works
 *    if the name outlives the process, which is why the caller's part file is
 *    stable per version (`magpiePartPath`) — on 11-11 this shipped with
 *    `.<pid>.part`, so every resume quietly started from zero.
 */
async function downloadTo(url: string, dest: string, timeoutMs = 30 * 60_000): Promise<void> {
  const base = [
    '-sS',
    '-L',
    '--fail',
    '--connect-timeout',
    '20',
    '--max-time',
    String(Math.ceil(timeoutMs / 1000)),
    '--speed-limit',
    '20480',
    '--speed-time',
    '60',
    '-C',
    '-',
    '-H',
    'Accept: application/octet-stream',
    '-o',
    dest,
    url
  ]

  const routes = [base, [...base.slice(0, -1), '--noproxy', '*', url]]
  let last: unknown = null

  // Three rounds, alternating proxy → direct. A flaky local proxy is the common
  // failure here; the direct run is the recovery, and a resumed second attempt
  // is cheap enough that one bad minute should not cost the user an update.
  for (let round = 0; round < 3; round += 1) {
    for (const args of routes) {
      try {
        await curl(args, timeoutMs)

        return
      } catch (error) {
        last = error
      }
    }
  }

  throw last instanceof Error ? last : new Error(String(last))
}

/** The downloader `downloadVerified` drives. A parameter so a test can fake the wire. */
export type MagpieDownload = (url: string, dest: string) => Promise<void>

/**
 * Download `url` into `dest` and prove the bytes are the ones `sha256` names.
 *
 * `-C -` cannot tell a truncated file from a poisoned one — it appends to
 * whatever is in `dest`. So one bad byte left by an earlier run fails the same
 * digest forever, and every retry resumes onto the same garbage: the update is
 * stuck, silently, with 74 MB on disk to show for it. One clean restart from
 * zero is the escape hatch. If that digest disagrees too, the asset and the
 * checksum file do not describe the same thing and no amount of retrying helps.
 */
export async function downloadVerified(
  url: string,
  dest: string,
  sha256: string,
  download: MagpieDownload = (target, file) => downloadTo(target, file)
): Promise<void> {
  const expected = sha256.toLowerCase()

  // A part that is already complete — the run that downloaded it died between the
  // last byte and the rename — would make `-C -` ask for bytes past the end and
  // collect a 416 on every retry. Verify what is on disk before spending the wire.
  if (fs.existsSync(dest) && (await sha256File(dest)) === expected) {return}

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await download(url, dest)

    const got = await sha256File(dest)

    if (got === expected) {return}

    // Suspect the bytes we may have resumed onto, not the server: start over once.
    fs.rmSync(dest, { force: true })

    if (attempt === 1) {
      throw new Error(`sha256 mismatch for ${path.basename(dest)}: expected ${expected}, got ${got}`)
    }
  }
}

/**
 * Pick one digest out of a `SHA256SUMS` body.
 *
 * The format is `<hex>  <name>` per line, but the two spaces are only a
 * convention: parse the first 64-hex token and the trailing name, so binary-mode
 * `*name` or extra spacing does not silently drop the line we need — a missing
 * digest must mean "cannot verify", never "verify passed".
 */
export function sha256FromSums(body: string, assetName: string): string | null {
  for (const line of body.split(/\r?\n/)) {
    const trimmed = line.trim()

    if (!trimmed) {continue}
    const match = trimmed.match(/^([0-9a-fA-F]{64})\s+\*?(.+)$/)

    if (!match) {continue}

    if (match[2].trim() !== assetName) {continue}

    return match[1].toLowerCase()
  }

  return null
}

/**
 * Ask upstream what the newest build is, resolved to this machine's asset.
 *
 * Note what is *not* used: `browser_download_url`. On the machine we measured,
 * that host times out while the API endpoint redirects to a host that answers,
 * so the API redirect is the path we keep.
 */
export async function fetchLatestRelease(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch
): Promise<MagpieRelease | null> {
  const assetName = magpieAssetName(platform, arch)

  if (!assetName) {return null}

  const body = await fetchText(`https://api.github.com/repos/${MAGPIE_UPSTREAM_REPO}/releases/latest`)

  const release = JSON.parse(body) as {
    tag_name?: string
    published_at?: string
    assets?: Array<{ id?: number; name?: string; size?: number }>
  }

  const version = release.tag_name ?? ''

  if (!VERSION_RE.test(version)) {return null}

  const assets = Array.isArray(release.assets) ? release.assets : []
  const asset = assets.find((entry) => entry.name === assetName)
  const sums = assets.find((entry) => entry.name === 'SHA256SUMS')

  if (!asset?.id || !sums?.id) {return null}

  // The digest lives in a release *file*, so this request wants bytes — with the
  // JSON accept header GitHub would answer with the asset's metadata instead and
  // every install would fail verification against `{`.
  const sumsBody = await fetchText(
    `https://api.github.com/repos/${MAGPIE_UPSTREAM_REPO}/releases/assets/${sums.id}`,
    'application/octet-stream'
  )

  const sha256 = sha256FromSums(sumsBody, assetName)

  if (!sha256) {return null}

  return {
    version,
    sha256,
    url: `https://api.github.com/repos/${MAGPIE_UPSTREAM_REPO}/releases/assets/${asset.id}`,
    size: typeof asset.size === 'number' ? asset.size : 0,
    publishedAt: typeof release.published_at === 'string' ? release.published_at : ''
  }
}

function writeJsonAtomic(file: string, value: unknown): void {
  const tmp = `${file}.${process.pid}.tmp`
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  fs.renameSync(tmp, file)
}

/**
 * Drop version directories beyond the newest few.
 *
 * 74 MB per build and nothing ever reaches for an old one: the pointer follows
 * exactly one version, `markBad` writes inside the version it names. Keeping two
 * (the one we just installed plus one step back) leaves a rollback with bytes on
 * disk, while a user who updates for a year does not quietly collect gigabytes.
 * A directory we cannot delete is left for the next install to collect.
 */
export function pruneOldBuilds(plobiHome: string, keepVersion: string, keep = 2): string[] {
  const root = magpieBinRoot(plobiHome)
  let entries: fs.Dirent[]

  try {
    entries = fs.readdirSync(root, { withFileTypes: true })
  } catch {
    return []
  }

  const versions = entries
    .filter((entry) => entry.isDirectory() && VERSION_RE.test(entry.name))
    .map((entry) => entry.name)
    .sort((a, b) => compareMagpieVersion(b, a))

  const survivors = new Set<string>([keepVersion])

  for (const version of versions) {
    if (survivors.size >= Math.max(1, keep)) {break}
    survivors.add(version)
  }

  const removed: string[] = []

  for (const version of versions) {
    if (survivors.has(version)) {continue}
    try {
      fs.rmSync(path.join(root, version), { recursive: true, force: true })
      removed.push(version)
    } catch {
      // locked by a running sidecar or a dying virus scanner
    }
  }

  return removed
}

/** Knobs `installBuild` accepts, all optional so the old call sites still read. */
export interface InstallBuildOptions {
  /** The packaged version — a download older than this is refused, not installed. */
  floor?: string | null
  /** How many version directories to keep on disk afterwards. */
  keep?: number
}

/**
 * Install a build and point `current.json` at it. Idempotent: a second call for
 * an already-installed, still-hash-matching version only rewrites the pointer.
 *
 * The order matters. Hash first, rename second, pointer last — so the sidecar
 * can never observe a version it would exec before the bytes are on disk and
 * verified, and a rejected digest leaves the previous pointer untouched.
 *
 * `floor` closes the hole the digest cannot: `SHA256SUMS` and the asset come
 * down the same channel, so a swapped channel serves both a bad binary and a
 * matching bad digest. A version *older* than the packaged one is not an update
 * by any reading of the word, so it is refused here.
 */
export async function installBuild(
  plobiHome: string,
  release: Pick<MagpieRelease, 'version' | 'url' | 'sha256'>,
  platform: NodeJS.Platform = process.platform,
  options: InstallBuildOptions = {}
): Promise<MagpieCurrent> {
  const { version, url } = release
  const sha256 = release.sha256.toLowerCase()

  if (!VERSION_RE.test(version)) {throw new Error(`not a magpie version: ${version}`)}

  if (!SHA256_RE.test(sha256)) {throw new Error(`not a sha256: ${sha256}`)}

  if (options.floor && compareMagpieVersion(version, options.floor) < 0) {
    throw new Error(`refusing ${version}: older than the packaged ${options.floor}`)
  }

  const root = magpieBinRoot(plobiHome)

  // Now that we know which version this install is for, the pile of part files
  // has exactly one member worth keeping: this version's. Everything else is a
  // fossil from a run that died, an install that finished, or a version that has
  // already been superseded — and nothing will ever read it again.
  sweepStaleParts(plobiHome, platform, version)

  const dir = path.join(root, version)
  const binary = path.join(dir, magpieExeName(platform))

  // Already here and still intact — trust the bytes on disk only after hashing them.
  if (fs.existsSync(binary)) {
    const existing = await sha256File(binary)

    if (existing === sha256) {
      const current: MagpieCurrent = {
        version,
        binary,
        sha256,
        size: fs.statSync(binary).size,
        installedAt: new Date().toISOString()
      }
      writeJsonAtomic(currentPointer(plobiHome), current)
      pruneOldBuilds(plobiHome, version, options.keep)

      return current
    }
  }

  fs.mkdirSync(root, { recursive: true })

  // Pid-free on purpose: the run that resumes these bytes is allowed to be a
  // different process than the one that started them. And deliberately *not*
  // cleaned up when the download fails — an interrupted download is the case
  // `-C -` exists for, and deleting the tail here would charge the next attempt
  // for all 74 MB over again. `sweepStaleParts` collects it once it is useless.
  const part = magpiePartPath(plobiHome, version)

  await downloadVerified(url, part, sha256)

  fs.mkdirSync(dir, { recursive: true })

  // Windows cannot rename onto an existing file; drop the stale one first.
  try {
    fs.rmSync(binary, { force: true })
  } catch {
    // a locked leftover surfaces on the rename below, which is where it belongs
  }

  fs.renameSync(part, binary)

  try {
    fs.chmodSync(binary, 0o755)
  } catch {
    // a filesystem without modes is fine
  }

  // A fresh install clears the old verdict on this version.
  fs.rmSync(badMarker(plobiHome, version), { force: true })

  const current: MagpieCurrent = {
    version,
    binary,
    sha256,
    size: fs.statSync(binary).size,
    installedAt: new Date().toISOString()
  }
  writeJsonAtomic(currentPointer(plobiHome), current)
  // Only after the pointer moved: a half-pruned directory is harmless, but a
  // pruned directory that was still current would not be. Same reason the prune
  // runs last rather than as the download starts.
  pruneOldBuilds(plobiHome, version, options.keep)

  return current
}

/*
 * There used to be an `updateIfChanged()` here: fetch the latest, install it if
 * the version string differed. Nothing ever called it — the IPC handler in
 * `main.ts` does exactly that with a user watching — and keeping a second,
 * uncalled entry point that downloads 74 MB is how "we never download silently"
 * quietly stops being true. Check and apply are the two doors; this is neither.
 */

/**
 * `v0.1.4572`, `0.1.4572` and `V0.1.4572` are one build.
 *
 * Two notations reach us and they had never been compared: the pointer and the
 * release tag carry the `v`, while `GET /api/hello` answers
 * `{"name":"magpie","version":"0.1.4572"}` (measured 2026-11-20). Normalised
 * here, once, so "is the port served by the build we just installed" is a
 * string comparison and not a guess.
 *
 * Returns the `v`-prefixed form — the shape everything else in this file uses —
 * or null when the string is not a version at all.
 */
export function normalizeMagpieVersion(value: null | string | undefined): null | string {
  if (typeof value !== 'string') {return null}

  const stripped = value.trim().replace(/^v/i, '')

  return /^\d+\.\d+\.\d+$/.test(stripped) ? `v${stripped}` : null
}

/**
 * Downloaded builds still on disk that we could run, newest first.
 *
 * This is the rollback list. When a freshly installed build refuses to serve,
 * the honest step back is the newest *other* build that already ran on this
 * machine — not the copy in the installer, which is v0.1.3403 while the user
 * may have been on v0.1.4371 for days.
 *
 * Left out on purpose: builds marked bad (they lost their right to run) and
 * builds at or below the packaged floor (that is a downgrade, not a rollback).
 */
export function listInstalledBuilds(plobiHome: string, platform: NodeJS.Platform = process.platform): MagpieBuild[] {
  const root = magpieBinRoot(plobiHome)
  const exe = magpieExeName(platform)
  const floor = packagedFloorVersion(platform)

  let entries: fs.Dirent[]

  try {
    entries = fs.readdirSync(root, { withFileTypes: true })
  } catch {
    return []
  }

  const builds: MagpieBuild[] = []

  for (const entry of entries) {
    if (!entry.isDirectory() || !VERSION_RE.test(entry.name)) {continue}

    const version = entry.name
    if (floor && compareMagpieVersion(version, floor) <= 0) {continue}
    if (fs.existsSync(badMarker(plobiHome, version))) {continue}

    const binary = path.join(root, version, exe)

    try {
      if (!fs.statSync(binary).isFile()) {continue}
    } catch {
      continue // a directory left behind by a failed install is not a build
    }

    builds.push({ version, binary, sha256: '' })
  }

  // Newest first: the first entry is the one worth going back to.
  return builds.sort((a, b) => compareMagpieVersion(b.version, a.version))
}

/**
 * Point `current.json` back at a build that is already on disk (a rollback).
 *
 * Refuses anything at or below the packaged floor for the same reason
 * `installBuild` does: "roll back" must never quietly become "downgrade below
 * what the installer shipped". Returns null when it refused, so the caller can
 * try the next candidate instead of starting a build it was not allowed to run.
 *
 * The digest is read from disk rather than remembered: a pointer that names a
 * hash nobody verified is how `readCurrent` starts rejecting its own pointer.
 */
export async function pointCurrentAt(
  plobiHome: string,
  build: { binary: string; sha256?: string; version: string },
  platform: NodeJS.Platform = process.platform
): Promise<MagpieCurrent | null> {
  if (!VERSION_RE.test(build.version)) {return null}

  const floor = packagedFloorVersion(platform)
  if (floor && compareMagpieVersion(build.version, floor) <= 0) {return null}

  let size: number

  try {
    size = fs.statSync(build.binary).size
  } catch {
    return null
  }

  const sha256 = build.sha256 && SHA256_RE.test(build.sha256) ? build.sha256 : await sha256File(build.binary)

  const current: MagpieCurrent = {
    version: build.version,
    binary: build.binary,
    sha256,
    size,
    installedAt: new Date().toISOString()
  }

  writeJsonAtomic(currentPointer(plobiHome), current)
  // A build we chose to run again has not lost its right to run.
  fs.rmSync(badMarker(plobiHome, build.version), { force: true })

  return current
}

