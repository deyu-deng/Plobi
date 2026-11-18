import assert from 'node:assert/strict'
import test from 'node:test'
import zlib from 'node:zlib'

import { checkBuffer, LAYOUT, readZipMember, sha256 } from './fetch-egress-assets.mjs'

/** 手搓一个只有单个成员的最小 zip：stored 或 deflate，够验证解析器。 */
function makeZip(name, body, { method = 0 } = {}) {
  const nameBytes = Buffer.from(name, 'utf8')
  const payload = method === 8 ? zlib.deflateRawSync(body) : body
  const local = Buffer.alloc(30)

  local.writeUInt32LE(0x04034b50, 0)
  local.writeUInt16LE(20, 4)
  local.writeUInt16LE(0, 6)
  local.writeUInt16LE(method, 8)
  local.writeUInt32LE(payload.length, 18)
  local.writeUInt32LE(body.length, 22)
  local.writeUInt16LE(nameBytes.length, 26)

  const localRecord = Buffer.concat([local, nameBytes, payload])
  const central = Buffer.alloc(46)

  central.writeUInt32LE(0x02014b50, 0)
  central.writeUInt16LE(20, 4)
  central.writeUInt16LE(20, 6)
  central.writeUInt16LE(0, 8)
  central.writeUInt16LE(method, 10)
  central.writeUInt32LE(payload.length, 20)
  central.writeUInt32LE(body.length, 24)
  central.writeUInt16LE(nameBytes.length, 28)
  central.writeUInt32LE(0, 42)

  const centralRecord = Buffer.concat([central, nameBytes])
  const eocd = Buffer.alloc(22)

  eocd.writeUInt32LE(0x06054b50, 0)
  eocd.writeUInt16LE(1, 8)
  eocd.writeUInt16LE(1, 10)
  eocd.writeUInt32LE(centralRecord.length, 12)
  eocd.writeUInt32LE(localRecord.length, 16)

  return Buffer.concat([localRecord, centralRecord, eocd])
}

test('readZipMember pulls a stored member out byte for byte', () => {
  const body = Buffer.from('mihomo binary payload', 'utf8')
  const zip = makeZip('mihomo-windows-amd64.exe', body)

  assert.deepEqual(readZipMember(zip, 'mihomo-windows-amd64.exe'), body)
})

test('readZipMember inflates a deflated member', () => {
  const body = Buffer.from('x'.repeat(4096), 'utf8')
  const zip = makeZip('wintun/bin/amd64/wintun.dll', body, { method: 8 })

  assert.deepEqual(readZipMember(zip, 'wintun/bin/amd64/wintun.dll'), body)
})

test('readZipMember names the member it could not find', () => {
  const zip = makeZip('only-this.txt', Buffer.from('x', 'utf8'))

  assert.throws(() => readZipMember(zip, 'mihomo.exe'), /没有 mihomo\.exe/)
})

test('readZipMember rejects a buffer that is not a zip', () => {
  assert.throws(() => readZipMember(Buffer.from('not a zip at all', 'utf8'), 'a'), /EOCD/)
})

test('checkBuffer pins length, digest and refuses an HTML error page', () => {
  const body = Buffer.from('payload', 'utf8')
  const digest = sha256(body)

  assert.equal(checkBuffer({ name: 'x', buffer: body, bytes: body.length, sha: digest }), digest)
  assert.throws(() => checkBuffer({ name: 'x', buffer: body, bytes: body.length + 1 }), /长度不符/)
  assert.throws(() => checkBuffer({ name: 'x', buffer: body, minBytes: body.length + 1 }), /下限/)
  assert.throws(() => checkBuffer({ name: 'x', buffer: body, sha: 'a'.repeat(64) }), /sha256 不符/)

  const page = Buffer.from('<!DOCTYPE html><html><body>404</body></html>', 'utf8')

  assert.throws(() => checkBuffer({ name: 'x', buffer: page }), /HTML/)
})

test('LAYOUT keeps mihomo and wintun in bin/, geo data at the root', () => {
  // 与 stage-egress-assets.mjs 的 EGRESS_ASSETS 是同一张表；两边错位的话包里会有个
  // 谁都找不到的 mihomo.exe。
  assert.deepEqual(LAYOUT, {
    'mihomo.exe': 'bin',
    'wintun.dll': 'bin',
    'GeoSite.dat': '',
    'geoip.metadb': ''
  })
})
