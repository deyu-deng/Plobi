import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { checkDistBuilt, checkBackendBinaries, requiredBackendBinaries } from '../scripts/assert-dist-built.mjs'

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
