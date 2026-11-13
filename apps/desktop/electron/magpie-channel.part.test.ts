/**
 * Tests for the magpie self-updater's part file — WP-MAGPIE-RESUME.
 *
 * Run with: npm run test:desktop:platforms
 *
 * What actually happened on 11-11, on the installed 0.3.1: `~/Plobi/magpie-runtime/bin/`
 * collected `v0.1.3771.34360.part` at 69 MB and no log line mentioned it. Two
 * causes, and each one is pinned below:
 *
 *  - the temp name ended in `.<pid>.part`, so the *next* process could never find
 *    the bytes the previous one left. `curl -C -` resumed from zero, so a killed
 *    run paid for 74 MB it already had — and the orphaned name was unreachable by
 *    the cleanup path too, because cleanup only ever walked version directories.
 *  - a poisoned part (one bad byte from a truncated run) failed the digest and
 *    then resumed onto the same garbage forever: the update was stuck, silently.
 */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test, { type TestContext } from 'node:test'

import {
  downloadVerified,
  magpieBinRoot,
  magpieExeName,
  magpiePartPath,
  sha256File,
  sweepStaleParts
} from './magpie-channel'

function digestOf(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex')
}

function tmpHome(t: TestContext): string {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'magpie-part-'))

  t.after(() => {
    fs.rmSync(home, { recursive: true, force: true })
  })

  return home
}

test('the part name carries the version and nothing about the process', (t) => {
  const home = tmpHome(t)

  const first = magpiePartPath(home, 'v0.1.3771')
  const second = magpiePartPath(home, 'v0.1.3771')

  assert.equal(first, second, 'same version must mean the same file, run after run')
  assert.equal(path.dirname(first), magpieBinRoot(home))
  assert.equal(path.basename(first), 'v0.1.3771.part')
  // The whole bug, in one assertion: nothing pid-shaped may enter the name.
  assert.equal(first.includes(`.${process.pid}.`), false)
})

test('bytes left by a killed run are resumed, not paid for twice', async (t) => {
  const home = tmpHome(t)
  const dest = magpiePartPath(home, 'v0.1.3771')
  const body = Buffer.from('magpie-sidecar-binary-'.repeat(64))
  const digest = digestOf(body)

  // Run A: four bytes down, then the process died. Only the bytes survive.
  fs.mkdirSync(path.dirname(dest), { recursive: true })
  fs.writeFileSync(dest, body.subarray(0, 4))

  // Run B stands in for the next process: it locates the file *because the name
  // does not mention run A's pid*, and asks the server only for the tail — which
  // is precisely what `curl -C -` does with a name that outlived its writer.
  let resumedAt = -1

  await downloadVerified('https://api.example.invalid/asset', dest, digest, async (_url, file) => {
    resumedAt = fs.existsSync(file) ? fs.statSync(file).size : 0

    const have = resumedAt > 0 ? fs.readFileSync(file) : Buffer.alloc(0)

    fs.writeFileSync(file, Buffer.concat([have, body.subarray(have.length)]))
  })

  assert.equal(resumedAt, 4, 'the second run must continue at byte 4, not at zero')
  assert.equal(await sha256File(dest), digest)
})

test('a poisoned part is thrown away once, then fetched from zero', async (t) => {
  const home = tmpHome(t)
  const dest = magpiePartPath(home, 'v0.1.3771')
  const body = Buffer.from('the-real-binary')
  const digest = digestOf(body)

  fs.mkdirSync(path.dirname(dest), { recursive: true })
  fs.writeFileSync(dest, Buffer.alloc(9, 0x2e)) // garbage a previous run left behind

  const seen: number[] = []

  await downloadVerified('https://api.example.invalid/asset', dest, digest, async (_url, file) => {
    // Append, the way `-C -` does: the garbage runs first, so the first attempt
    // produces a longer file that still cannot match the digest.
    const have = fs.existsSync(file) ? fs.readFileSync(file) : Buffer.alloc(0)

    seen.push(have.length)
    fs.writeFileSync(file, Buffer.concat([have, body]))
  })

  assert.deepEqual(seen, [9, 0], 'first attempt resumes onto the garbage, the second starts clean')
  assert.equal(await sha256File(dest), digest)
})

test('a digest that never matches fails loudly and leaves no part behind', async (t) => {
  const home = tmpHome(t)
  const dest = magpiePartPath(home, 'v0.1.3771')

  fs.mkdirSync(path.dirname(dest), { recursive: true })

  let calls = 0

  await assert.rejects(
    downloadVerified('https://api.example.invalid/asset', dest, digestOf(Buffer.from('expected')), async (_url, file) => {
      calls += 1
      fs.writeFileSync(file, Buffer.from('expected-but-wrong'))
    }),
    /sha256 mismatch for v0\.1\.3771\.part/
  )

  assert.equal(calls, 2, 'one resumed attempt, one clean attempt, then stop')
  assert.equal(fs.existsSync(dest), false, 'a part that can never verify is not left to be resumed again')
})

test('a part that is already the right bytes is not downloaded again', async (t) => {
  const home = tmpHome(t)
  const dest = magpiePartPath(home, 'v0.1.3771')
  const body = Buffer.from('complete-from-an-earlier-run')

  fs.mkdirSync(path.dirname(dest), { recursive: true })
  fs.writeFileSync(dest, body)

  let touched = false

  await downloadVerified('https://api.example.invalid/asset', dest, digestOf(body), async () => {
    touched = true
  })

  assert.equal(touched, false, '`-C -` on a complete file asks for bytes past the end; skip the wire')
})

test('sweepStaleParts clears the fossils and keeps the one worth resuming', (t) => {
  const home = tmpHome(t)
  const bin = magpieBinRoot(home)

  fs.mkdirSync(bin, { recursive: true })

  // the real corpse: version plus pid, from before the naming fix
  fs.writeFileSync(path.join(bin, 'v0.1.3771.34360.part'), 'x')

  // a version that is already installed — the rename happened, only the copy stayed
  const installed = path.join(bin, 'v0.1.3700')

  fs.mkdirSync(installed, { recursive: true })
  fs.writeFileSync(path.join(installed, magpieExeName('win32')), 'bin')
  fs.writeFileSync(path.join(bin, 'v0.1.3700.part'), 'x')

  // interrupted and never installed: this is the resume point, leave it alone
  fs.writeFileSync(path.join(bin, 'v0.1.3800.part'), 'x')

  // not a part file at all
  fs.writeFileSync(path.join(bin, 'current.json'), '{}')

  const removed = sweepStaleParts(home, 'win32').sort()

  assert.deepEqual(removed, ['v0.1.3700.part', 'v0.1.3771.34360.part'])
  assert.equal(fs.existsSync(path.join(bin, 'v0.1.3800.part')), true)
  assert.equal(fs.existsSync(path.join(bin, 'current.json')), true)
})

test('sweeping with a target version drops every other version, too', (t) => {
  const home = tmpHome(t)
  const bin = magpieBinRoot(home)

  fs.mkdirSync(bin, { recursive: true })
  fs.writeFileSync(path.join(bin, 'v0.1.3800.part'), 'x')
  fs.writeFileSync(path.join(bin, 'v0.1.3900.part'), 'x')

  const removed = sweepStaleParts(home, 'win32', 'v0.1.3900')

  assert.deepEqual(removed, ['v0.1.3800.part'])
  assert.equal(fs.existsSync(path.join(bin, 'v0.1.3900.part')), true)
})
