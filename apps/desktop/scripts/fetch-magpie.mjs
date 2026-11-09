/**
 * Fetch the pinned magpie binary into the build tree (build/magpie/).
 *
 * magpie is the vendor-adapter layer we ship with (MIT, github.com/yetone/magpie).
 * The pin lives in ONE place — `Code/third_party/magpie.json` (version + asset +
 * sha256 + url) — and the release-watch bot bumps it when upstream ships a new
 * one. Nothing is downloaded at install time: the binary is fetched here, at
 * build time, and the hash is checked before it is put anywhere near a package.
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

function fail(message) {
  console.error(`[fetch-magpie] ${message}`)
  process.exit(1)
}

if (!fs.existsSync(pinPath)) fail(`the pin is missing: ${pinPath}`)
const pin = JSON.parse(fs.readFileSync(pinPath, 'utf8'))
if (!pin.url || !pin.sha256) fail('the pin has no url/sha256 — refusing to fetch an unpinned binary')

if (!force && fs.existsSync(outFile)) {
  const digest = createHash('sha256').update(fs.readFileSync(outFile)).digest('hex')
  if (digest.toLowerCase() === String(pin.sha256).toLowerCase()) {
    console.log(`[fetch-magpie] already here and matches the pin (${pin.version})`)
    process.exit(0)
  }
  console.log('[fetch-magpie] the copy on disk does not match the pin — refetching')
}

fs.mkdirSync(outDir, { recursive: true })

const response = await fetch(pin.url, { redirect: 'follow' })
if (!response.ok) fail(`download failed: HTTP ${response.status} ${pin.url}`)
const bytes = Buffer.from(await response.arrayBuffer())

const digest = createHash('sha256').update(bytes).digest('hex')
if (digest.toLowerCase() !== String(pin.sha256).toLowerCase()) {
  fail(`sha256 mismatch: expected ${pin.sha256}, got ${digest} — refusing to keep it`)
}

fs.writeFileSync(outFile, bytes)
console.log(`[fetch-magpie] ${pin.version} -> ${outFile} (${bytes.length} bytes, sha256 ok)`)
