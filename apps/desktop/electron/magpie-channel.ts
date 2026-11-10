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
 *    `<PLOBI_HOME>/magpie/bin/<version>/`; `resources/magpie/` is left alone, so
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
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

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

/** `<PLOBI_HOME>/magpie/bin` — the writable home of downloaded builds. */
export function magpieBinRoot(plobiHome: string): string {
  return path.join(plobiHome, 'magpie', 'bin')
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

  if (typeof version !== 'string' || !VERSION_RE.test(version)) {return null}

  if (typeof binary !== 'string' || !path.isAbsolute(binary)) {return null}

  if (!binary.startsWith(magpieBinRoot(plobiHome) + path.sep)) {return null}

  if (typeof sha256 !== 'string' || !SHA256_RE.test(sha256)) {return null}

  if (fs.existsSync(badMarker(plobiHome, version))) {return null}

  try {
    if (!fs.statSync(binary).isFile()) {return null}
  } catch {
    return null
  }

  return { version, binary, sha256, installedAt }
}

/** The downloaded build the sidecar should prefer over the packaged one. */
export function resolveUpdatedBuild(plobiHome: string): MagpieBuild | null {
  const current = readCurrent(plobiHome)

  return current ? { version: current.version, binary: current.binary, sha256: current.sha256 } : null
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

/** Download `url` to `dest`, proxy first then direct, `Accept` tuned for assets. */
async function downloadTo(url: string, dest: string, timeoutMs = 30 * 60_000): Promise<void> {
  const base = [
    '-sS',
    '-L',
    '--fail',
    '--max-time',
    String(Math.ceil(timeoutMs / 1000)),
    '-H',
    'Accept: application/octet-stream',
    '-o',
    dest,
    url
  ]

  try {
    await curl(base, timeoutMs)
  } catch {
    await curl([...base.slice(0, -1), '--noproxy', '*', url], timeoutMs)
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
 * Install a build and point `current.json` at it. Idempotent: a second call for
 * an already-installed, still-hash-matching version only rewrites the pointer.
 *
 * The order matters. Hash first, rename second, pointer last — so the sidecar
 * can never observe a version it would exec before the bytes are on disk and
 * verified, and a rejected digest leaves the previous pointer untouched.
 */
export async function installBuild(
  plobiHome: string,
  release: Pick<MagpieRelease, 'version' | 'url' | 'sha256'>,
  platform: NodeJS.Platform = process.platform
): Promise<MagpieCurrent> {
  const { version, url } = release
  const sha256 = release.sha256.toLowerCase()

  if (!VERSION_RE.test(version)) {throw new Error(`not a magpie version: ${version}`)}

  if (!SHA256_RE.test(sha256)) {throw new Error(`not a sha256: ${sha256}`)}

  const dir = path.join(magpieBinRoot(plobiHome), version)
  const binary = path.join(dir, magpieExeName(platform))

  // Already here and still intact — trust the bytes on disk only after hashing them.
  if (fs.existsSync(binary)) {
    const existing = await sha256File(binary)

    if (existing === sha256) {
      const current: MagpieCurrent = { version, binary, sha256, installedAt: new Date().toISOString() }
      writeJsonAtomic(currentPointer(plobiHome), current)

      return current
    }
  }

  fs.mkdirSync(magpieBinRoot(plobiHome), { recursive: true })
  const part = path.join(magpieBinRoot(plobiHome), `${version}.${process.pid}.part`)

  try {
    await downloadTo(url, part)
    const got = await sha256File(part)

    if (got !== sha256) {throw new Error(`sha256 mismatch for ${version}: expected ${sha256}, got ${got}`)}

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

    const current: MagpieCurrent = { version, binary, sha256, installedAt: new Date().toISOString() }
    writeJsonAtomic(currentPointer(plobiHome), current)

    return current
  } finally {
    fs.rmSync(part, { force: true })
  }
}

/**
 * Check upstream and install, if there is something newer than `current`.
 *
 * Returns the build that is now current, or null when nothing changed.
 * Deliberately no version comparison here: the caller decides whether to move,
 * so "install the version I was told to" (a rollback, a pinned build) stays
 * expressible.
 */
export async function updateIfChanged(
  plobiHome: string,
  currentVersion: string | null,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch
): Promise<MagpieCurrent | null> {
  const latest = await fetchLatestRelease(platform, arch)

  if (!latest) {return null}

  if (currentVersion && latest.version === currentVersion) {return null}

  return installBuild(plobiHome, latest, platform)
}
