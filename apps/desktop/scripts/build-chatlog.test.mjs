import assert from 'node:assert/strict'
import test from 'node:test'

import { looksRunnable } from './build-chatlog.mjs'

// The macOS build produces a Mach-O. The old check only knew `MZ` (Windows) and
// `\x7fELF` (Linux), so a perfectly good 41 MB Mach-O was rejected as "not a
// runnable binary" after the build succeeded.

test('accepts a Windows PE binary', () => {
  assert.equal(looksRunnable('MZ\x90\x00'), true)
})

test('accepts an ELF binary', () => {
  assert.equal(looksRunnable('\x7fELF'), true)
})

test('accepts a 64-bit Mach-O binary', () => {
  assert.equal(looksRunnable('\xcf\xfa\xed\xfe'), true)
})

test('accepts a 32-bit Mach-O binary', () => {
  assert.equal(looksRunnable('\xce\xfa\xed\xfe'), true)
})

test('accepts a Mach-O universal (fat) binary', () => {
  assert.equal(looksRunnable('\xca\xfe\xba\xbe'), true)
})

test('rejects a Go archive (not a runnable binary)', () => {
  assert.equal(looksRunnable('PK\x03\x04'), false)
  assert.equal(looksRunnable('\x01\x02\x03\x04'), false)
  assert.equal(looksRunnable(''), false)
})
