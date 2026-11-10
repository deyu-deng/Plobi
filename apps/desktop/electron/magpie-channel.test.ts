/**
 * Behavior tests for the self-update path — real bytes over real HTTP, real
 * files on a temp `PLOBI_HOME`, no mocks of the parts that can lie.
 *
 * What must hold, and why each one is a test rather than a comment:
 *
 *   - a digest that does not match the bytes we got is a hard stop (the whole
 *     reason this module exists is that the binary it installs runs with the
 *     user's credentials);
 *   - a rejected install leaves the previous pointer untouched (a user with a
 *     working build must not be downgraded to "no vendor layer");
 *   - `markBad` really removes a build from consideration (that is the fallback
 *     the sidecar leans on);
 *   - the pointer cannot be hand-edited into a path outside our bin root (it is
 *     a plain JSON file on a user's disk).
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  installBuild,
  magpieAssetName,
  magpieBinRoot,
  markBad,
  readCurrent,
  resolveUpdatedBuild,
  sha256FromSums
} from './magpie-channel'

function tempHome(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-magpie-'))
}

/** A server that hands out one body and counts how often it was asked. */
async function serveBytes(body: Buffer): Promise<{ url: string; hits: () => number; close: () => void }> {
  let hits = 0

  const server = createServer((_req, res) => {
    hits += 1
    res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': body.length })
    res.end(body)
  })

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0

  return {
    url: `http://127.0.0.1:${port}/magpie-cli.exe`,
    hits: () => hits,
    close: () => server.close()
  }
}

const digest = (buffer: Buffer): string => createHash('sha256').update(buffer).digest('hex')

test('sha256FromSums takes the line for our asset and nothing else', () => {
  const sums = [
    '0ff757aa64865072909676a90d142967821dfc969b8362838e4a2735c14b23fd  magpie-cli-windows-amd64.exe',
    '9692446c0967d7bf062a13e54a3644432dc6f93215b3d3db83ac4bba59bb5854  *magpie-windows-amd64.exe',
    '',
    'not a digest line'
  ].join('\n')

  assert.equal(
    sha256FromSums(sums, 'magpie-cli-windows-amd64.exe'),
    '0ff757aa64865072909676a90d142967821dfc969b8362838e4a2735c14b23fd'
  )
  // Binary-mode `*name` is still our asset.
  assert.equal(
    sha256FromSums(sums, 'magpie-windows-amd64.exe'),
    '9692446c0967d7bf062a13e54a3644432dc6f93215b3d3db83ac4bba59bb5854'
  )
  // A missing digest must read as "cannot verify", never as a pass.
  assert.equal(sha256FromSums(sums, 'magpie-cli-linux-amd64'), null)
})

test('the asset we ask upstream for is the CLI build, not the desktop app', () => {
  assert.equal(magpieAssetName('win32', 'x64'), 'magpie-cli-windows-amd64.exe')
  assert.equal(magpieAssetName('darwin', 'arm64'), 'magpie-cli-darwin-arm64')
  assert.equal(magpieAssetName('linux', 'x64'), 'magpie-cli-linux-amd64')
})

test('install writes the bytes, records the pointer, and is idempotent', async () => {
  const home = tempHome()
  const body = Buffer.from('#!/bin/sh\necho magpie\n', 'utf8')
  const server = await serveBytes(body)

  try {
    const release = { version: 'v0.1.9999', url: server.url, sha256: digest(body) }
    const first = await installBuild(home, release, 'win32')

    assert.equal(first.version, 'v0.1.9999')
    assert.equal(first.binary, path.join(magpieBinRoot(home), 'v0.1.9999', 'magpie-cli.exe'))
    assert.deepEqual(fs.readFileSync(first.binary), body)
    assert.equal(server.hits(), 1, 'the binary should be fetched once')

    // The pointer describes the build we just installed.
    const current = readCurrent(home)
    assert.equal(current?.version, 'v0.1.9999')
    assert.equal(current?.sha256, digest(body))
    assert.deepEqual(resolveUpdatedBuild(home)?.binary, first.binary)

    // A second call for the same version re-hashes what is on disk instead of
    // downloading 74 MB again.
    const second = await installBuild(home, release, 'win32')
    assert.equal(second.version, 'v0.1.9999')
    assert.equal(server.hits(), 1, 'an already-installed build must not be re-downloaded')
  } finally {
    server.close()
  }
})

test('a digest mismatch installs nothing and keeps the previous pointer', async () => {
  const home = tempHome()
  const good = Buffer.from('good build', 'utf8')
  const server = await serveBytes(good)

  try {
    await installBuild(home, { version: 'v0.1.1000', url: server.url, sha256: digest(good) }, 'win32')
    const before = readCurrent(home)

    // Same bytes, a digest that does not describe them: exactly the shape of a
    // swapped or truncated download.
    await assert.rejects(
      installBuild(home, { version: 'v0.1.1001', url: server.url, sha256: 'a'.repeat(64) }, 'win32'),
      /sha256 mismatch/
    )

    assert.equal(readCurrent(home)?.version, 'v0.1.1000', 'the working build stays current')
    assert.deepEqual(readCurrent(home), before)
    assert.equal(fs.existsSync(path.join(magpieBinRoot(home), 'v0.1.1001')), false)
    // No half-written download left behind for the next boot to trip on.
    assert.deepEqual(
      fs.readdirSync(magpieBinRoot(home)).filter((name) => name.endsWith('.part')),
      []
    )
  } finally {
    server.close()
  }
})

test('markBad takes a build out of the running, and the pointer can be cleared', async () => {
  const home = tempHome()
  const body = Buffer.from('build that will not start', 'utf8')
  const server = await serveBytes(body)

  try {
    await installBuild(home, { version: 'v0.1.1002', url: server.url, sha256: digest(body) }, 'win32')
    assert.equal(resolveUpdatedBuild(home)?.version, 'v0.1.1002')

    markBad(home, 'v0.1.1002', 'no answer on 3425 within 8s')

    assert.equal(readCurrent(home), null, 'a bad build must read as absent')
    assert.equal(resolveUpdatedBuild(home), null)
    // The version directory survives, so we can still look at it.
    assert.equal(fs.existsSync(path.join(magpieBinRoot(home), 'v0.1.1002', '.bad')), true)
  } finally {
    server.close()
  }
})

test('a hand-edited pointer cannot point outside our bin root', async () => {
  const home = tempHome()
  const outside = path.join(tempHome(), 'elsewhere.exe')
  fs.writeFileSync(outside, 'not ours', 'utf8')

  const root = magpieBinRoot(home)
  fs.mkdirSync(root, { recursive: true })
  const version = 'v0.1.1003'
  fs.mkdirSync(path.join(root, version), { recursive: true })
  fs.writeFileSync(path.join(root, version, 'magpie-cli.exe'), 'ours', 'utf8')
  fs.writeFileSync(
    path.join(root, 'current.json'),
    JSON.stringify({ version, binary: outside, sha256: 'a'.repeat(64), installedAt: '' }),
    'utf8'
  )

  assert.equal(readCurrent(home), null, 'a path outside <home>/magpie/bin is not executable by us')
})
