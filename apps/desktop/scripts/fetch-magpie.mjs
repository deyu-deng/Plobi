/**
 * Fetch the pinned magpie binary into the build tree (build/magpie/).
 *
 * magpie is the vendor-adapter layer we ship with (MIT, github.com/yetone/magpie).
 * The pin lives in ONE place — `Code/third_party/magpie.json`; its `assets` map
 * carries one line per platform, and this script picks the line that matches the
 * machine doing the build (`process.platform` + `process.arch`).
 *
 * That per-platform lookup is the whole point: the first version of this script
 * kept the Windows URL and only renamed the file, so a macOS build shipped a
 * Windows PE named `magpie-cli`. Now a platform the pin does not cover fails the
 * build loudly instead of producing a binary that cannot run.
 *
 * Nothing is downloaded at install time: the binary is fetched here, at build
 * time, and the hash is checked before it gets anywhere near a package.
 *
 *   node scripts/fetch-magpie.mjs            # fetch if missing
 *   node scripts/fetch-magpie.mjs --force    # refetch even if present
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(here, '..')
const repoRoot = path.resolve(desktopRoot, '..', '..')
const pinPath = path.join(repoRoot, 'third_party', 'magpie.json')
const outDir = path.join(desktopRoot, 'build', 'magpie')
const outFile = path.join(outDir, process.platform === 'win32' ? 'magpie-cli.exe' : 'magpie-cli')

const force = process.argv.includes('--force')

/** `win32` + `x64` → `windows-x64`. The names the pin uses. */
function platformKey() {
  const os = { win32: 'windows', darwin: 'darwin', linux: 'linux' }[process.platform]
  const arch = { x64: 'x64', arm64: 'arm64' }[process.arch]
  return os && arch ? `${os}-${arch}` : null
}

function fail(message) {
  console.error(`[fetch-magpie] ${message}`)
  process.exit(1)
}

if (!fs.existsSync(pinPath)) fail(`the pin is missing: ${pinPath}`)
const pin = JSON.parse(fs.readFileSync(pinPath, 'utf8'))
const key = platformKey()
if (!key) fail(`this build machine is ${process.platform}/${process.arch}, which the pin has no line for`)
const entry = (pin.assets || {})[key]
if (!entry) {
  fail(`the pin (${pin.version}) carries no ${key} asset — only: ${Object.keys(pin.assets || {}).join(', ')}`)
}
if (!entry.url || !entry.sha256) fail(`the pin's ${key} line has no url/sha256 — refusing to fetch an unpinned binary`)
const expected = String(entry.sha256).toLowerCase()

if (!force && fs.existsSync(outFile)) {
  const digest = createHash('sha256').update(fs.readFileSync(outFile)).digest('hex')
  if (digest.toLowerCase() === expected) {
    console.log(`[fetch-magpie] already here and matches the pin (${pin.version}, ${key})`)
    process.exit(0)
  }
  console.log('[fetch-magpie] the copy on disk does not match the pin — refetching')
}

fs.mkdirSync(outDir, { recursive: true })

const response = await fetch(entry.url, { redirect: 'follow' })
if (!response.ok) fail(`download failed: HTTP ${response.status} ${entry.url}`)
const bytes = Buffer.from(await response.arrayBuffer())

const digest = createHash('sha256').update(bytes).digest('hex')
if (digest.toLowerCase() !== expected) {
  fail(`sha256 mismatch for ${key}: expected ${expected}, got ${digest} — refusing to keep it`)
}

fs.writeFileSync(outFile, bytes)
if (process.platform !== 'win32') fs.chmodSync(outFile, 0o755)
console.log(`[fetch-magpie] ${pin.version} ${key} -> ${outFile} (${bytes.length} bytes, sha256 ok)`)
