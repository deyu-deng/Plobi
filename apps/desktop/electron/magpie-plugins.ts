/**
 * The magpie **plugin set** that ships with the app, and the one rule that
 * governs it: the packaged set is a **floor**, never a ceiling.
 *
 * Why the set ships at all (measured 2026-11-10 on this machine): a fresh
 * install has a magpie with **no providers**. An empty data directory, the host
 * boots, writes its settings and its icons — and publishes an empty provider
 * roster. Every one of the 22 subscription cards then reads "not installed",
 * the login method has no name to point at, and the `+` cannot open a browser
 * round trip. magpie does **not** install its plugins by itself: the host we
 * started on an empty directory wrote no `plugins.json` and no
 * `plugin-providers.json` at all.
 *
 * The same shape the binary has, deliberately:
 *
 *   packaged  `resources/magpie-plugins` after packaging, `build/magpie-plugins`
 *             in a dev tree — the directory `fetch-magpie-plugins.mjs` fills.
 *             It is what the installer shipped: a floor, the version the app
 *             knows how to work with.
 *   runtime   `<PLOBI_HOME>/magpie` — what magpie itself writes and manages.
 *             It may be **newer** than the packaged set (magpie updates its own
 *             plugins; the channel can also update the binary on its own), and
 *             that is fine.
 *
 * So the seeding rule is one line: **seed only into a data directory that has
 * no plugin set at all, and never into one that does.** A machine that already
 * has plugins keeps them; a machine whose magpie was updated on its own keeps
 * the set that magpie manages. The packaged set is what a *fresh* install
 * starts from — never something we push back onto a machine that has moved on.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { compareMagpieVersion } from './magpie-channel'

const HERE = path.dirname(fileURLToPath(import.meta.url))

/** One plugin as the pin lists it. */
export interface MagpiePluginEntry {
  spec: string
  version: string
  sha256: string
  source: 'npm' | 'vendored'
  tarball: string
  off?: boolean
}

/** The packaged plugin set, read from the directory the build filled. */
export interface MagpiePluginSet {
  setVersion: string
  /** The magpie version this set was verified against. */
  magpie: string
  plugins: MagpiePluginEntry[]
  /** magpie's provider roster, already stripped of per-machine state. */
  roster: unknown[] | null
}

/**
 * Where the packaged set lives: `resources/magpie-plugins` after packaging,
 * `build/magpie-plugins` in a dev tree.
 *
 * `process.resourcesPath` also exists under plain `electron .`, pointing at
 * electron's own resources, so the packaged path only wins when it actually
 * holds a `version.json`.
 */
export function packagedMagpiePluginsDir(): string {
  const resources = process.resourcesPath ?? ''
  const packaged = resources ? path.join(resources, 'magpie-plugins') : ''

  if (packaged && fs.existsSync(path.join(packaged, 'version.json'))) {return packaged}

  return path.resolve(HERE, '..', 'build', 'magpie-plugins')
}

/** magpie's own data directory: `$XDG_CONFIG_HOME/magpie` with XDG pinned to PLOBI_HOME. */
export function magpiePluginsDir(plobiHome: string): string {
  return path.join(plobiHome, 'magpie')
}

/** Read the packaged set — or null when this build carries none. */
export function readPackagedPluginSet(dir: string): MagpiePluginSet | null {
  let manifest: unknown
  let marker: unknown

  try {
    manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'))
    marker = JSON.parse(fs.readFileSync(path.join(dir, 'version.json'), 'utf8'))
  } catch {
    return null
  }

  if (!manifest || typeof manifest !== 'object') {return null}
  const value = manifest as Record<string, unknown>
  const plugins = Array.isArray(value.plugins) ? (value.plugins as MagpiePluginEntry[]) : []

  if (!plugins.length) {return null}

  let roster: unknown[] | null = null

  try {
    const parsed = JSON.parse(fs.readFileSync(path.join(dir, 'plugin-providers.json'), 'utf8'))

    if (Array.isArray(parsed)) {roster = parsed}
  } catch {
    roster = null
  }

  const mark = (marker && typeof marker === 'object' ? marker : {}) as Record<string, unknown>

  return {
    setVersion: String(mark.set_version || value.set_version || ''),
    magpie: String(mark.magpie || value.magpie || ''),
    plugins,
    roster
  }
}

/**
 * magpie's own plugin list format — the specs it resolves on boot.
 *
 * The versions are **ours** (the pin), not `@latest`: a package that installs
 * whatever the registry says today is not the package the build was verified
 * against.
 */
export function magpiePluginManifest(plugins: MagpiePluginEntry[]): string {
  const rows = plugins.map(entry => ({
    spec: `${entry.spec}@${entry.version}`,
    ...(entry.off ? { off: true } : {})
  }))

  return `${JSON.stringify({ plugins: rows }, null, 1)}\n`
}

/**
 * Per-machine state that must never travel inside the installer.
 *
 * magpie's own roster carries the **login state** of the machine that produced
 * it. Shipping that as-is would put somebody's sign-in state inside the
 * package. The catalog half — ids, names, apis, login *methods*, model lists —
 * is what we ship; the state half is dropped.
 */
const ROSTER_STATE_KEYS = ['signedIn', 'authType', 'accountId', 'accounts']

/** The roster with every per-machine field removed. */
export function sanitizeRoster(rows: unknown): unknown[] {
  if (!Array.isArray(rows)) {return []}

  return rows.map(row => {
    const clean: Record<string, unknown> = {}

    for (const [key, value] of Object.entries((row || {}) as Record<string, unknown>)) {
      if (!ROSTER_STATE_KEYS.includes(key)) {clean[key] = value}
    }

    return clean
  })
}

export interface SeedResult {
  seeded: boolean
  reason: string
  /** Where the set landed (only when it was seeded). */
  dataDir?: string
}

/**
 * Put the packaged plugin set into a machine that has none.
 *
 * The rules, in the order they are checked:
 *
 *  1. this build carries no set → nothing to do (a dev tree without a build);
 *  2. the data directory already has a plugin set → **never touch it**;
 *  3. the magpie we are about to run is **newer** than the packaged one → do not
 *     push an older set at it: that magpie came from the channel and manages
 *     its own plugins, and rolling its set back is the one thing that would
 *     make an independently updated magpie misbehave;
 *  4. otherwise: write the plugin list, copy the tarballs where magpie looks
 *     for them (`plugins/dist/`), and copy the roster.
 *
 * The tarballs land under **magpie's own file names** — that is the name its
 * installer produces, so its resolver finds them the same way it would find
 * ones it had downloaded itself.
 */
export function seedMagpiePlugins(
  plobiHome: string,
  options: { packagedDir?: string; runningMagpieVersion?: string } = {}
): SeedResult {
  const packagedDir = options.packagedDir ?? packagedMagpiePluginsDir()
  const set = readPackagedPluginSet(packagedDir)

  if (!set) {
    return { seeded: false, reason: 'this build carries no plugin set' }
  }

  const dataDir = magpiePluginsDir(plobiHome)
  const listPath = path.join(dataDir, 'plugins.json')

  if (fs.existsSync(listPath)) {
    return { seeded: false, reason: 'this machine already has a plugin set — left alone' }
  }

  if (options.runningMagpieVersion && set.magpie) {
    const order = compareMagpieVersion(options.runningMagpieVersion, set.magpie)

    if (order > 0) {
      return {
        seeded: false,
        reason: `magpie ${options.runningMagpieVersion} is newer than the packaged set (${set.magpie}) — its own plugins stay`
      }
    }
  }

  const distDir = path.join(dataDir, 'plugins', 'dist')
  fs.mkdirSync(distDir, { recursive: true })

  let copied = 0

  for (const entry of set.plugins) {
    const source = path.join(packagedDir, 'plugins', 'dist', entry.tarball)

    if (!fs.existsSync(source)) {continue}
    fs.copyFileSync(source, path.join(distDir, entry.tarball))
    copied += 1
  }

  fs.writeFileSync(listPath, magpiePluginManifest(set.plugins), 'utf8')

  if (set.roster) {
    // Sanitized on the way out, not only on the way in: the build already
    // strips this, but the file that lands on the user's disk is the one that
    // must not carry anybody's sign-in state.
    fs.writeFileSync(path.join(dataDir, 'plugin-providers.json'), `${JSON.stringify(sanitizeRoster(set.roster), null, 1)}\n`, 'utf8')
  }

  // A marker, so a later boot knows this machine's set came from us — and so
  // nobody mistakes the packaged set for something the user configured.
  fs.writeFileSync(
    path.join(dataDir, 'plugins-set.json'),
    `${JSON.stringify({ set_version: set.setVersion, magpie: set.magpie, plugins: copied, seeded_by: 'packaged' }, null, 1)}\n`,
    'utf8'
  )

  return { seeded: true, reason: `seeded plugin set ${set.setVersion} (${copied} plugins)`, dataDir }
}

export default {
  packagedMagpiePluginsDir,
  magpiePluginsDir,
  readPackagedPluginSet,
  magpiePluginManifest,
  sanitizeRoster,
  seedMagpiePlugins
}
