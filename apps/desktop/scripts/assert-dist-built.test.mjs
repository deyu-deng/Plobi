import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  checkDistBuilt,
  checkBackendBinaries,
  checkMagpieBinary,
  requiredBackendBinaries,
  requiredMagpieBinary
} from '../scripts/assert-dist-built.mjs'

function makeDist(extra) {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-assert-dist-'))
  const distDir = path.join(tempRoot, 'dist')
  fs.mkdirSync(distDir, { recursive: true })
  if (extra) extra(distDir)
  return { tempRoot, distDir }
}

test('checkDistBuilt passes when index.html + an assets JS bundle exist', () => {
  const { tempRoot, distDir } = makeDist(d => {
    fs.writeFileSync(path.join(d, 'index.html'), '<!doctype html><div id=root></div>', 'utf8')
    fs.mkdirSync(path.join(d, 'assets'))
    fs.writeFileSync(path.join(d, 'assets', 'index-abc123.js'), 'console.log(1)', 'utf8')
  })
  try {
    assert.deepEqual(checkDistBuilt(distDir), { ok: true })
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkDistBuilt fails when the dist directory is absent', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-assert-dist-'))
  try {
    const result = checkDistBuilt(path.join(tempRoot, 'dist'))
    assert.equal(result.ok, false)
    assert.match(result.error, /no dist directory/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkDistBuilt fails when index.html is missing', () => {
  const { tempRoot, distDir } = makeDist(d => {
    fs.mkdirSync(path.join(d, 'assets'))
    fs.writeFileSync(path.join(d, 'assets', 'index-abc123.js'), 'console.log(1)', 'utf8')
  })
  try {
    const result = checkDistBuilt(distDir)
    assert.equal(result.ok, false)
    assert.match(result.error, /index\.html is missing/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkDistBuilt fails when index.html is empty', () => {
  const { tempRoot, distDir } = makeDist(d => {
    fs.writeFileSync(path.join(d, 'index.html'), '', 'utf8')
    fs.mkdirSync(path.join(d, 'assets'))
    fs.writeFileSync(path.join(d, 'assets', 'index-abc123.js'), 'console.log(1)', 'utf8')
  })
  try {
    const result = checkDistBuilt(distDir)
    assert.equal(result.ok, false)
    assert.match(result.error, /index\.html is empty/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkDistBuilt fails when assets/ has no JS bundle', () => {
  const { tempRoot, distDir } = makeDist(d => {
    fs.writeFileSync(path.join(d, 'index.html'), '<!doctype html>', 'utf8')
    fs.mkdirSync(path.join(d, 'assets'))
    // CSS only, no JS — still a blank page at runtime.
    fs.writeFileSync(path.join(d, 'assets', 'index-abc123.css'), 'body{}', 'utf8')
  })
  try {
    const result = checkDistBuilt(distDir)
    assert.equal(result.ok, false)
    assert.match(result.error, /no built JS bundle/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

// --- backend binaries -------------------------------------------------------
// The vendored collector's bin/ is gitignored, so a fresh checkout has no
// binary. Nothing failed at build time; the feature was just dead for whoever
// installed the package. Same contract as the renderer check above.
function makeBackend(platform, write) {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-assert-backend-'))
  const backendDir = path.join(tempRoot, 'backend')
  const rel = requiredBackendBinaries(platform)[0].rel
  const full = path.join(backendDir, rel)
  fs.mkdirSync(path.dirname(full), { recursive: true })
  if (write) write(full)
  return { tempRoot, backendDir }
}

// 2 MB of a real-looking header is enough: the check is magic bytes + size, so a
// Go archive (or a zero-byte placeholder) cannot pass as the collector.
function writeFakeBinary(full, magic) {
  const buf = Buffer.alloc(2 * 1024 * 1024)
  buf.write(magic, 0, 'latin1')
  fs.writeFileSync(full, buf)
}

test('checkBackendBinaries passes when the collector binary is a real PE/ELF', () => {
  const { tempRoot, backendDir } = makeBackend('win32', full => writeFakeBinary(full, 'MZ'))
  try {
    assert.deepEqual(checkBackendBinaries(backendDir, 'win32'), { ok: true, problems: [] })
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkBackendBinaries fails when the collector binary is absent', () => {
  const { tempRoot, backendDir } = makeBackend('win32', null)
  try {
    const result = checkBackendBinaries(backendDir, 'win32')
    assert.equal(result.ok, false)
    assert.equal(result.problems.length, 1)
    assert.equal(result.problems[0].reason, 'missing')
    assert.match(result.problems[0].rel, /chatlog\.exe$/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkBackendBinaries rejects a Go archive that go build may leave behind', () => {
  // `go build -o` writes "!<arch>" and exits 0 when the target is not `main` --
  // exactly the mistake that made the option-less build look successful.
  const { tempRoot, backendDir } = makeBackend('win32', full => writeFakeBinary(full, '!<ar'))
  try {
    const result = checkBackendBinaries(backendDir, 'win32')
    assert.equal(result.ok, false)
    assert.match(result.problems[0].reason, /not a runnable binary/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkBackendBinaries expects no extension off Windows', () => {
  const { tempRoot, backendDir } = makeBackend('linux', full => writeFakeBinary(full, '\x7fELF'))
  try {
    assert.equal(checkBackendBinaries(backendDir, 'linux').ok, true)
    assert.match(requiredBackendBinaries('linux')[0].rel, /bin[\\/]chatlog$/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

// The magpie binary is *downloaded* by fetch-magpie.mjs, so nothing upstream of
// packaging would notice a failed fetch: the package would simply offer a vendor
// layer that cannot start. And the pin used to carry only a Windows asset, so a
// macOS build packaged a Windows PE — present, plausible, unrunnable.
function makeMagpie(platform, write) {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-assert-magpie-'))
  const buildDir = path.join(tempRoot, 'build')
  const rel = requiredMagpieBinary(platform).rel
  const full = path.join(buildDir, rel)
  fs.mkdirSync(path.dirname(full), { recursive: true })
  if (write) {
    write(full)
    // fetch-magpie.mjs now names the copy it ships. Without that name the app
    // cannot tell this installer's build from one downloaded months ago, so a
    // build tree with a binary and no version.json is exactly what the check
    // exists to refuse.
    fs.writeFileSync(path.join(path.dirname(full), 'version.json'), '{"version":"v0.1.3403"}')
  }
  return { tempRoot, buildDir }
}

// 21 MB: over the "is this really the 70 MB binary" floor, cheap enough to write.
function writeFakeMagpie(full, magic) {
  const buf = Buffer.alloc(21 * 1024 * 1024)
  buf.write(magic, 0, 'latin1')
  fs.writeFileSync(full, buf)
}

test('checkMagpieBinary passes for a Windows build with a real PE beside it', () => {
  const { tempRoot, buildDir } = makeMagpie('win32', full => writeFakeMagpie(full, 'MZ'))
  try {
    assert.deepEqual(checkMagpieBinary(buildDir, 'win32'), { ok: true, problems: [] })
    assert.match(requiredMagpieBinary('win32').rel, /magpie[\\/]magpie-cli\.exe$/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary fails when the fetch never ran', () => {
  const { tempRoot, buildDir } = makeMagpie('win32', null)
  try {
    const result = checkMagpieBinary(buildDir, 'win32')
    assert.equal(result.ok, false)
    assert.equal(result.problems[0].reason, 'missing')
    assert.match(result.error, /magpie binary missing/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary fails a macOS build that downloaded the Windows asset', () => {
  // The exact bug the per-platform pin exists to stop: the file is named for
  // this platform (`magpie-cli`) but its bytes are a Windows PE.
  const { tempRoot, buildDir } = makeMagpie('darwin', full => writeFakeMagpie(full, 'MZ'))
  try {
    const result = checkMagpieBinary(buildDir, 'darwin')
    assert.equal(result.ok, false)
    assert.match(result.problems[0].reason, /built for another platform/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary fails a truncated download instead of trusting its name', () => {
  const { tempRoot, buildDir } = makeMagpie('linux', full => writeFakeMagpie(full, '\x7fELF'))
  try {
    // 21 MB is the pass case; a 4 MB stub is what a cut-off fetch leaves behind.
    // It keeps the header (so it is not caught earlier as "not an executable")
    // and is caught only by the size floor.
    const stub = Buffer.alloc(4 * 1024 * 1024)
    stub.write('\x7fELF', 0, 'latin1')
    fs.writeFileSync(path.join(buildDir, requiredMagpieBinary('linux').rel), stub)
    const result = checkMagpieBinary(buildDir, 'linux')
    assert.equal(result.ok, false)
    assert.match(result.problems[0].reason, /too small/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary accepts the macOS Mach-O build with no extension', () => {
  const { tempRoot, buildDir } = makeMagpie('darwin', full => writeFakeMagpie(full, '\xcf\xfa\xed\xfe'))
  try {
    assert.deepEqual(checkMagpieBinary(buildDir, 'darwin'), { ok: true, problems: [] })
    assert.match(requiredMagpieBinary('darwin').rel, /magpie[\\/]magpie-cli$/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary fails a binary the build never named', () => {
  // The 0.3.0 tree has fetched magpie since day one and never wrote a version
  // beside it, so this is the state a stale checkout packages: a perfectly good
  // 74 MB binary that the app cannot compare against a downloaded one.
  const { tempRoot, buildDir } = makeMagpie('win32', full => writeFakeMagpie(full, 'MZ'))
  try {
    fs.rmSync(path.join(buildDir, 'magpie', 'version.json'))
    const result = checkMagpieBinary(buildDir, 'win32')
    assert.equal(result.ok, false)
    assert.match(result.error, /version\.json is missing/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkMagpieBinary fails a version it cannot compare', () => {
  const { tempRoot, buildDir } = makeMagpie('win32', full => writeFakeMagpie(full, 'MZ'))
  try {
    fs.writeFileSync(path.join(buildDir, 'magpie', 'version.json'), '{"version":"3403"}')
    const result = checkMagpieBinary(buildDir, 'win32')
    assert.equal(result.ok, false)
    assert.match(result.error, /no usable version/)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('checkBackendBinaries accepts the macOS Mach-O build', () => {
  // The macOS collector is a Mach-O.  The check used to demand ELF on every
  // non-Windows platform, so the mac packaging died over a binary that was
  // right there (2026-11-07 CI: run 42359753097, "Build mac" red).
  const { tempRoot, backendDir } = makeBackend('darwin', full => writeFakeBinary(full, '\xcf\xfa\xed\xfe'))
  try {
    assert.deepEqual(checkBackendBinaries(backendDir, 'darwin'), { ok: true, problems: [] })
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
