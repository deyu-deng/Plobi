#!/usr/bin/env node
/**
 * Collect the WINDOWS toolchain into the desktop package: PortableGit (git + bash)
 * and ripgrep.
 *
 * Why this exists (2026-11-07, user verdict): "为什么我们的软件下载起来这么复杂，
 * 和别人的一点都不一样" —— everybody else's installer ships the runtime; ours made
 * the FIRST LAUNCH download it. The backend already travels inside the package
 * (runtime source + locked wheels, see collect-backend-bundle.mjs); the toolchain
 * did not, so a fresh machine still reached out to github.com mid-launch, and a
 * machine whose only Git lives on D:\ looked "uninstallable".
 *
 * With these two files in the package:
 *   - the first launch needs NO download for git/bash/rg  ⇒ works offline;
 *   - the agent's terminal tool has a bash of its own, on the chosen data drive.
 *
 * Only Windows needs this: macOS and Linux ship git and bash in the base system.
 * On those platforms this script writes nothing (and says so).
 *
 * Usage: node scripts/collect-toolchain-bundle.mjs [--out <dir>]
 *   --out defaults to <desktop>/build/toolchain
 */

import fs from 'node:fs'
import path from 'node:path'
import https from 'node:https'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = path.resolve(HERE, '..')

const args = process.argv.slice(2)
const outIdx = args.indexOf('--out')
const OUT_DIR =
  outIdx >= 0 && args[outIdx + 1]
    ? path.resolve(args[outIdx + 1])
    : path.join(DESKTOP_ROOT, 'build', 'toolchain')

/**
 * Pinned versions — they MUST match the ones install.ps1 downloads when the
 * package has no toolchain (a dev-machine install, or a non-Windows leg).
 */
const PORTABLE_GIT = {
  tag: 'v2.54.0.windows.1',
  version: '2.54.0',
  asset: 'PortableGit-2.54.0-64-bit.7z.exe'
}

const RIPGREP = {
  version: '14.1.1',
  asset: 'ripgrep-14.1.1-x86_64-pc-windows-msvc.zip'
}

/**
 * uv — the package manager that installs the backend's dependencies and creates
 * its virtualenv.  install.ps1 otherwise pulls it from astral.sh on first launch
 * (a fresh machine would still need the network just for this).
 */
const UV = {
  tag: '0.13.1',
  asset: 'uv-x86_64-pc-windows-msvc.zip'
}

/**
 * CPython 3.11 — the interpreter the backend's virtualenv is built on.  Same
 * reason as uv: `uv python install` would fetch it on first launch.  This is the
 * python-build-standalone "install_only" archive (the same build uv itself
 * would pick), unpacked into <data dir>\python by the installer.
 */
const CPYTHON = {
  tag: '20261026',
  version: '3.11.18'
}

/**
 * The assets differ per platform: Windows ships .zip, the others .tar.gz, and
 * the target triple differs too.  Everything is pinned — nothing here may
 * float on "latest".
 */
function platformAssets() {
  const arch = process.arch
  if (process.platform === 'win32') {
    const triple = arch === 'arm64' ? 'aarch64-pc-windows-msvc' : 'x86_64-pc-windows-msvc'
    return {
      triple,
      uv: `uv-${triple}.zip`,
      rg: `ripgrep-${RIPGREP.version}-${triple}.zip`,
      py: `cpython-${CPYTHON.version}+${CPYTHON.tag}-${triple}-install_only.tar.gz`,
      // git is not part of the base system on Windows -> it travels with us.
      needsGit: true,
      needsRg: true
    }
  }
  if (process.platform === 'darwin') {
    const triple = arch === 'arm64' ? 'aarch64-apple-darwin' : 'x86_64-apple-darwin'
    return {
      triple,
      uv: `uv-${triple}.tar.gz`,
      py: `cpython-${CPYTHON.version}+${CPYTHON.tag}-${triple}-install_only.tar.gz`,
      // macOS and Linux ship git; ripgrep is not part of their base system but
      // the POSIX installer never installed it either -> leave it alone.
      needsGit: false,
      needsRg: false
    }
  }
  const triple = arch === 'arm64' ? 'aarch64-unknown-linux-gnu' : 'x86_64-unknown-linux-gnu'
  return {
    triple,
    uv: `uv-${triple}.tar.gz`,
    py: `cpython-${CPYTHON.version}+${CPYTHON.tag}-${triple}-install_only.tar.gz`,
    needsGit: false,
    needsRg: false
  }
}

function say(msg) {
  console.log(`[collect-toolchain-bundle] ${msg}`)
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'plobi-desktop-build' } }, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume()
        resolve(download(res.headers.location, dest))
        return
      }
      if (res.statusCode !== 200) {
        res.resume()
        reject(new Error(`HTTP ${res.statusCode} for ${url}`))
        return
      }
      const tmp = `${dest}.part`
      const out = fs.createWriteStream(tmp)
      res.pipe(out)
      out.on('finish', () => {
        out.close()
        fs.renameSync(tmp, dest)
        resolve()
      })
      out.on('error', reject)
    })
    req.on('error', reject)
  })
}

function sizeMB(file) {
  return (fs.statSync(file).size / 1048576).toFixed(1)
}

const ASSETS = platformAssets()
say(`platform: ${process.platform}/${process.arch} → ${ASSETS.triple}`)

fs.rmSync(OUT_DIR, { recursive: true, force: true })
fs.mkdirSync(OUT_DIR, { recursive: true })

const targets = []
if (ASSETS.needsGit) {
  targets.push({
    url: `https://github.com/git-for-windows/git/releases/download/${PORTABLE_GIT.tag}/${PORTABLE_GIT.asset}`,
    file: path.join(OUT_DIR, PORTABLE_GIT.asset)
  })
}
if (ASSETS.needsRg) {
  targets.push({
    url: `https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP.version}/${ASSETS.rg}`,
    file: path.join(OUT_DIR, ASSETS.rg)
  })
}
targets.push({
  url: `https://github.com/astral-sh/uv/releases/download/${UV.tag}/${ASSETS.uv}`,
  file: path.join(OUT_DIR, ASSETS.uv)
})
targets.push({
  url: `https://github.com/astral-sh/python-build-standalone/releases/download/${CPYTHON.tag}/${ASSETS.py}`,
  file: path.join(OUT_DIR, ASSETS.py)
})

for (const target of targets) {
  if (fs.existsSync(target.file) && fs.statSync(target.file).size > 1024 * 1024) {
    say(`already here: ${path.basename(target.file)} (${sizeMB(target.file)} MB)`)
    continue
  }
  say(`downloading ${path.basename(target.file)} ...`)
  try {
    await download(target.url, target.file)
  } catch (err) {
    // A missing toolchain is not a broken build: the installer still downloads
    // it on first launch. Say it loudly and let the build continue.
    say(`WARNING: could not fetch ${path.basename(target.file)} (${err.message}) — the first launch will download it instead`)
    continue
  }
  say(`  ${path.basename(target.file)}  ${sizeMB(target.file)} MB`)
}

const entries = fs.readdirSync(OUT_DIR)
say(`wrote ${OUT_DIR} (${entries.length} files)`)
