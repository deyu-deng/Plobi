#!/usr/bin/env node
/**
 * Build the vendored chatlog binary -- the engine behind the WeChat message
 * collector (`plobi/collectors/chatlog`).
 *
 * Why this exists: the binary is a Go build artefact and `*.exe` is gitignored
 * inside the vendored tree (`vendor/chatlog/.gitignore`), so a fresh checkout --
 * and therefore a CI runner -- has NO `bin/chatlog(.exe)`. Every packaged
 * installer built from such a checkout shipped without it, and the collector
 * came up as "unavailable" on the user's machine no matter what they did. The
 * fix is to build it as part of the packaging chain instead of hoping it is
 * already lying around (collect-backend-bundle.mjs now refuses to pack without
 * it; release-desktop.yml builds it in a dedicated job).
 *
 * Usage:
 *   node scripts/build-chatlog.mjs            # builds only when stale
 *   node scripts/build-chatlog.mjs --force    # always rebuild
 *
 * Environment:
 *   GO_BINARY / PLOBI_CHATLOG_GO   path to the go toolchain (default: `go` on PATH)
 *   CC / PLOBI_CHATLOG_CC          path to the C compiler (Windows; CGO needs one)
 *
 * Exit codes: 0 = built or already up to date, 1 = cannot build (says why).
 */

import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = path.resolve(HERE, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')
const SRC_DIR = path.join(REPO_ROOT, 'plobi', 'collectors', 'chatlog', 'vendor', 'chatlog')
const BIN_DIR = path.join(SRC_DIR, 'bin')

const IS_WINDOWS = process.platform === 'win32'
const BIN_NAME = IS_WINDOWS ? 'chatlog.exe' : 'chatlog'
const OUT = path.join(BIN_DIR, BIN_NAME)

const args = process.argv.slice(2)
const FORCE = args.includes('--force')

/**
 * Executable magics we accept, by platform-agnostic bytes.
 *
 * PE (Windows) starts with `MZ`; ELF (Linux, and macOS when cross-built) starts with
 * 0x7F 'E' 'L' 'F'; Mach-O (macOS native) starts with one of 0xFEEDFACF (64-bit),
 * 0xFEEDFACE (32-bit) or 0xCAFEBABE (universal/fat) -- on disk little-endian for the
 * first two, big-endian for the fat one.
 *
 * The macOS build is a Mach-O: the old check only knew MZ/ELF, so a **41 MB Mach-O was
 * rejected as "not a runnable binary"** after a perfectly good build. Accepting the
 * three Mach-O magics fixes it; we no longer branch on the platform at all, because
 * a cross-built ELF on Windows would be just as wrong to reject.
 */
const RUNNABLE_MAGICS = [
  'MZ', // PE / Windows
  '\x7fELF', // ELF / Linux
  '\xcf\xfa\xed\xfe', // Mach-O 64-bit (0xFEEDFACF)
  '\xce\xfa\xed\xfe', // Mach-O 32-bit (0xFEEDFACE)
  '\xca\xfe\xba\xbe', // Mach-O universal / fat (0xCAFEBABE)
]

/** True when the first bytes look like a runnable native binary. */
export function looksRunnable(magic) {
  return RUNNABLE_MAGICS.some((known) => magic.startsWith(known))
}

function say(msg) {
  console.log(`[build-chatlog] ${msg}`)
}

function die(msg) {
  console.error(`\n✗ build-chatlog: ${msg}\n`)
  process.exit(1)
}

/** First existing file out of a list of candidate paths. */
function firstFile(candidates) {
  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      if (fs.statSync(candidate).isFile()) return candidate
    } catch {
      /* not there */
    }
  }
  return null
}

/**
 * Resolve an executable through PATH.
 *
 * `where`/`which` are used rather than scanning PATH by hand: they honour
 * PATHEXT on Windows (so `gcc` finds `gcc.exe`) and skip directories the user
 * cannot read.
 */
function onPath(exe) {
  const tool = IS_WINDOWS ? 'where' : 'which'
  const result = spawnSync(tool, [exe], { encoding: 'utf8' })
  if (result.status !== 0) return null
  const line = (result.stdout || '')
    .split(/\r?\n/)
    .map(text => text.trim())
    .filter(Boolean)[0]
  return line || null
}

function findGo() {
  const explicit = process.env.PLOBI_CHATLOG_GO || process.env.GO_BINARY
  const found =
    firstFile([explicit]) ||
    onPath('go') ||
    firstFile([
      IS_WINDOWS ? 'D:/Runtimes/Languages/Go/bin/go.exe' : '/usr/local/go/bin/go',
      IS_WINDOWS ? 'C:/Program Files/Go/bin/go.exe' : '/usr/lib/go/bin/go',
      IS_WINDOWS ? 'C:/Go/bin/go.exe' : '/opt/homebrew/bin/go'
    ])
  if (!found) return null
  const version = spawnSync(found, ['version'], { encoding: 'utf8' })
  if (version.status !== 0) return null
  say(`go: ${found} (${(version.stdout || '').trim()})`)
  return found
}

/**
 * The vendored tree needs CGO (`go build` with CGO_ENABLED=1 links the WeChat
 * database reader), so Windows needs a mingw gcc. Nobody guarantees one is on
 * PATH -- this machine keeps it under D:\Runtimes -- so the usual install roots
 * are probed too. An explicit CC always wins.
 */
function findCC() {
  if (!IS_WINDOWS) return process.env.CC || 'cc'

  const explicit = process.env.PLOBI_CHATLOG_CC || process.env.CC
  const candidates = [
    'D:/Runtimes/Languages/mingw-w64/bin/gcc.exe',
    'C:/msys64/mingw64/bin/gcc.exe',
    'C:/msys64/ucrt64/bin/gcc.exe',
    'C:/msys64/clang64/bin/clang.exe',
    'C:/mingw64/bin/gcc.exe',
    'C:/TDM-GCC-64/bin/gcc.exe',
    'C:/ProgramData/mingw64/mingw64/bin/gcc.exe',
    'C:/ProgramData/chocolatey/bin/gcc.exe',
    'C:/ProgramData/chocolatey/lib/mingw/tools/install/bin/gcc.exe',
    `${process.env.USERPROFILE || 'C:/Users/Default'}/scoop/apps/mingw/current/bin/gcc.exe`
  ]
  const found =
    firstFile([explicit]) || onPath('gcc') || firstFile(candidates)
  if (!found) {
    say(`no C compiler found; tried: ${[explicit, ...candidates].filter(Boolean).join(', ')}`)
    return null
  }
  say(`cc: ${found}`)
  return found
}

/** Newest mtime among the files that decide whether the binary is stale. */
function newestSourceMtime(dir) {
  let newest = 0
  const walk = current => {
    let entries = []
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      if (entry.name === 'bin' || entry.name === 'dist' || entry.name === '.git') continue
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) {
        walk(full)
      } else if (/\.(go|mod|sum)$/.test(entry.name)) {
        try {
          newest = Math.max(newest, fs.statSync(full).mtimeMs)
        } catch {
          /* raced away */
        }
      }
    }
  }
  walk(dir)
  return newest
}

function main() {
  if (!fs.existsSync(SRC_DIR)) {
    die(
      `the vendored chatlog project is not at ${SRC_DIR}.\n` +
        '  It is a source snapshot taken from the upstream fork; see\n' +
        `  ${path.join(SRC_DIR, 'VENDOR.md')} for how it is re-synced.`
    )
  }

  if (!FORCE) {
    try {
      const built = fs.statSync(OUT).mtimeMs
      const newest = newestSourceMtime(SRC_DIR)
      if (newest && built >= newest) {
        say(`${path.relative(REPO_ROOT, OUT)} is up to date (${(fs.statSync(OUT).size / 1048576).toFixed(1)} MB)`)
        say('pass --force to rebuild anyway')
        return
      }
    } catch {
      /* no binary yet -- build it */
    }
  }

  const go = findGo()
  if (!go) {
    die(
      'no Go toolchain found, so the WeChat collector engine cannot be built.\n' +
        '  Install Go 1.21+ (https://go.dev/dl/) or point GO_BINARY at go.exe,\n' +
        '  then run: npm run build:chatlog'
    )
  }

  const cc = findCC()
  if (!cc) {
    die(
      'no C compiler found; the collector engine needs CGO.\n' +
        '  On a CI runner, the image does NOT ship mingw-w64 -- install it first:\n' +
        '    - uses: msys2/setup-msys2@v2\n' +
        '      with:\n' +
        '        msystem: MINGW64\n' +
        '        install: mingw-w64-x86_64-gcc\n' +
        '    (the compiler then lands at C:/msys64/mingw64/bin/gcc.exe, which this\n' +
        '    script finds on its own)\n' +
        '  Locally: install mingw-w64 (or MSYS2) and put gcc on PATH, or point CC at gcc.exe,\n' +
        '  then run: npm run build:chatlog'
    )
  }

  fs.mkdirSync(BIN_DIR, { recursive: true })

  // The entry point is the root `main.go` (package main) -- the same file upstream's
  // Makefile builds. `cmd/chatlog/` is package `chatlog`, a library: pointing
  // `go build -o` at it silently produces a Go archive instead of an executable.
  const entry = path.join(SRC_DIR, 'main.go')
  if (!fs.existsSync(entry)) {
    die(`no entry point at ${entry}; the vendored snapshot looks incomplete (see VENDOR.md).`)
  }
  const target = 'main.go'

  say(`building ${path.relative(REPO_ROOT, OUT)} from ${target} ...`)
  const started = Date.now()
  // Windows: a mingw-linked CGO binary dynamically imports libgcc_s_seh-1.dll,
  // libstdc++-6.dll and libwinpthread-1.dll -- those exist on the CI runner but
  // **not on the user's machine**, so the packaged collector would die with
  // "DLL not found" even though the build was green. Link the runtime
  // statically so the exe is self-contained. (Linux/macOS link against the
  // system libc, which every user machine has -- no need there.)
  const ldflags = IS_WINDOWS ? '-extldflags "-static" -w -s' : '-w -s'
  const build = spawnSync(go, ['build', '-trimpath', `-ldflags=${ldflags}`, '-o', OUT, target], {
    cwd: SRC_DIR,
    stdio: 'inherit',
    env: { ...process.env, CGO_ENABLED: '1', CC: cc }
  })

  if (build.error) die(`could not start go: ${build.error.message}`)
  if (build.status !== 0) die(`go build failed (exit ${build.status}); see the compiler output above.`)

  // `go build -o` happily writes a Go archive when the target package is not
  // `main` and still exits 0, so check the magic bytes rather than trusting the
  // exit code: MZ on Windows, ELF elsewhere. (This exact trap cost an hour.)
  let magic = ''
  let size = 0
  try {
    const fd = fs.openSync(OUT, 'r')
    const head = Buffer.alloc(4)
    fs.readSync(fd, head, 0, 4, 0)
    fs.closeSync(fd)
    magic = head.toString('latin1')
    size = fs.statSync(OUT).size
  } catch {
    /* fall through to the hard failure below */
  }
  const looksRunnableBinary = looksRunnable(magic)
  if (!looksRunnableBinary) {
    die(
      `go build reported success but ${OUT} is not a runnable binary ` +
        `(magic ${JSON.stringify(magic)}, ${size} B).\n` +
        `  Usually this means -o pointed at a non-main package; the entry point here is main.go.`
    )
  }

  say(`built in ${((Date.now() - started) / 1000).toFixed(1)}s: ${path.relative(REPO_ROOT, OUT)} (${(size / 1048576).toFixed(1)} MB)`)
}

// Only build when this file is *run* -- the test imports looksRunnable from it.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main()
}
