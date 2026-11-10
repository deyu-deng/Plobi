/**
 * Fetch the pinned magpie **plugin set** into the build tree (build/magpie-plugins/).
 *
 * The magpie binary is the vendor layer; the plugins are the vendor adapters it
 * loads. Without them a fresh install has a magpie with **no providers at all** —
 * measured 2026-11-10 on this machine: an empty data directory, the host boots,
 * writes its settings and icons, and every one of the 22 subscription cards reads
 * "not installed" (the roster it publishes is empty, so the login method has no
 * name and the `+` cannot be pointed at a browser round trip).
 *
 * The set is pinned in ONE place — `Code/third_party/magpie-plugins.json` — the
 * same way the binary is pinned in `third_party/magpie.json`: one line per
 * plugin with its version and the sha256 of its tarball. Every tarball is
 * **checked into the repo** (third_party/magpie-plugins/tarballs/), so this
 * script never touches the network: a build that cannot find one of them fails
 * loudly instead of quietly fetching whatever the registry says today. The
 * pin's `source` field says where each tarball originally came from (the public
 * npm registry, or magpie's own marketplace for the six that exist only there)
 * — provenance, not a place to fetch from.
 *
 * Nothing is downloaded at install time. The plugin **code** still needs the npm
 * registry for its own dependencies on first boot (that is the same network the
 * vendor logins need), but the plugin set itself — which plugins, which
 * versions, and their provider roster — ships with the app.
 *
 *   node scripts/fetch-magpie-plugins.mjs            # fill the build tree
 *   node scripts/fetch-magpie-plugins.mjs --force    # recopy even if present
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(here, '..')
const repoRoot = path.resolve(desktopRoot, '..', '..')

const PIN_REL = path.join('third_party', 'magpie-plugins.json')
const VENDOR_REL = path.join('third_party', 'magpie-plugins')
const OUT_REL = path.join('build', 'magpie-plugins')

function fail(message) {
  console.error(`[fetch-magpie-plugins] ${message}`)
  process.exit(1)
}

function sha256File(file) {
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

function sha256Bytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

/**
 * Read the pin. It is the only source of truth: which plugins, which versions,
 * and what each tarball's hash must be.
 */
export function readPluginPin(repoRootDir = repoRoot) {
  const pinPath = path.join(repoRootDir, PIN_REL)
  if (!fs.existsSync(pinPath)) fail(`the pin is missing: ${pinPath}`)
  const pin = JSON.parse(fs.readFileSync(pinPath, 'utf8'))
  if (!Array.isArray(pin.plugins) || pin.plugins.length === 0) {
    fail(`the pin (${pinPath}) lists no plugins`)
  }
  for (const entry of pin.plugins) {
    if (!entry.spec || !entry.version || !entry.sha256 || !entry.tarball) {
      fail(`the pin has an incomplete plugin line: ${JSON.stringify(entry)}`)
    }
  }
  return pin
}

/**
 * The provider roster, with every per-machine field removed.
 *
 * magpie's own `plugin-providers.json` carries the **login state** of the
 * machine that produced it (`signedIn`, `authType`, `accountId`, `accounts`).
 * Shipping that file as-is would put somebody's sign-in state inside the
 * installer. The catalog half — ids, names, apis, login *methods*, model lists —
 * is what we ship; the state half is dropped.
 */
const STATE_KEYS = ['signedIn', 'authType', 'accountId', 'accounts']

export function sanitizeRoster(rows) {
  if (!Array.isArray(rows)) fail('the roster is not a list')
  return rows.map(row => {
    const clean = {}
    for (const [key, value] of Object.entries(row)) {
      if (!STATE_KEYS.includes(key)) clean[key] = value
    }
    return clean
  })
}

/**
 * Fetch (or copy) every tarball, verify its hash, and lay the set out where the
 * runtime expects it.
 *
 * The layout mirrors magpie's own, because magpie reads it:
 *
 *   build/magpie-plugins/plugins/dist/<tarball>   the plugin code
 *   build/magpie-plugins/plugin-providers.json     the roster (magpie's file name)
 *   build/magpie-plugins/manifest.json             our pin (what we shipped)
 *   build/magpie-plugins/version.json              the marker the runtime reads
 */
export async function fetchPluginSet({
  repoRootDir = repoRoot,
  outDir = path.join(desktopRoot, OUT_REL),
  pin = null,
  force = false
} = {}) {
  const thePin = pin || readPluginPin(repoRootDir)
  const distDir = path.join(outDir, 'plugins', 'dist')
  fs.mkdirSync(distDir, { recursive: true })

  const problems = []

  for (const entry of thePin.plugins) {
    const target = path.join(distDir, entry.tarball)
    const vendored = path.join(repoRootDir, VENDOR_REL, 'tarballs', entry.tarball)

    if (!fs.existsSync(vendored)) {
      problems.push(`${entry.spec}: the checked-in tarball is missing (${vendored})`)
      continue
    }

    const bytes = fs.readFileSync(vendored)
    const digest = sha256Bytes(bytes)

    // A checked-in blob is not a trusted one: the pin says what it must hash to.
    if (digest.toLowerCase() !== entry.sha256.toLowerCase()) {
      problems.push(`${entry.spec}: the checked-in tarball does not match the pin's sha256`)
      continue
    }

    if (!force && fs.existsSync(target)) {
      const onDisk = sha256File(target)
      if (onDisk.toLowerCase() === entry.sha256.toLowerCase()) continue
      fs.rmSync(target, { force: true })
    }

    fs.writeFileSync(target, bytes)
  }

  // The roster: read from the repo (it is ours, checked in), hash-checked
  // against the pin, and written out under magpie's own file name.
  const rosterSource = path.join(repoRootDir, VENDOR_REL, 'roster.json')
  if (!fs.existsSync(rosterSource)) {
    problems.push(`the roster is missing (${rosterSource})`)
  } else {
    const raw = fs.readFileSync(rosterSource, 'utf8')
    const digest = sha256Bytes(Buffer.from(raw, 'utf8'))
    const expected = thePin.roster && thePin.roster.sha256
    if (expected && digest.toLowerCase() !== String(expected).toLowerCase()) {
      problems.push('the roster does not match the pin\'s sha256')
    } else {
      const rows = sanitizeRoster(JSON.parse(raw))
      fs.writeFileSync(path.join(outDir, 'plugin-providers.json'), `${JSON.stringify(rows, null, 1)}\n`, 'utf8')
    }
  }

  // Our own manifest (what we shipped) + the marker the runtime reads.
  fs.writeFileSync(path.join(outDir, 'manifest.json'), `${JSON.stringify(thePin, null, 1)}\n`, 'utf8')
  const marker = {
    set_version: thePin.set_version || 'unversioned',
    magpie: thePin.magpie || '',
    plugins: thePin.plugins.length,
    roster_sha256: (thePin.roster && thePin.roster.sha256) || ''
  }
  fs.writeFileSync(path.join(outDir, 'version.json'), `${JSON.stringify(marker, null, 2)}\n`, 'utf8')

  return { outDir, problems, marker }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const force = process.argv.includes('--force')
  const result = await fetchPluginSet({ force })
  if (result.problems.length) {
    console.error(`[fetch-magpie-plugins] ${result.problems.length} problem(s):`)
    for (const problem of result.problems) console.error(`  - ${problem}`)
    process.exit(1)
  }
  console.log(
    `[fetch-magpie-plugins] plugin set ${result.marker.set_version} ready in ${result.outDir} ` +
    `(${result.marker.plugins} plugins, magpie ${result.marker.magpie || 'unpinned'})`
  )
}

export default { fetchPluginSet, readPluginPin, sanitizeRoster }
