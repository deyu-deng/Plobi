/**
 * payload-size.mjs 的判据测试：预算判定只认"实测存在且超了"这一种情况。
 *
 * 跑法：node --test scripts/payload-size.test.mjs
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import { applyJunkStrip, evaluatePayload, formatReport, PAYLOAD_BUDGETS } from './payload-size.mjs'

const MB = 1024 * 1024

test('没超预算的项不报警', () => {
  const rows = evaluatePayload([{ key: 'toolchain', present: true, bytes: 150 * MB }])
  const toolchain = rows.find((row) => row.key === 'toolchain')
  assert.equal(toolchain.overBudget, false)
  assert.equal(Math.round(toolchain.megabytes), 150)
})

test('超了预算的项点名', () => {
  const rows = evaluatePayload([{ key: 'wheelhouse', present: true, bytes: 90 * MB }])
  const wheelhouse = rows.find((row) => row.key === 'wheelhouse')
  assert.equal(wheelhouse.overBudget, true)
})

test('构建没跑出来的项不算超预算 —— 缺活儿不是干坏了', () => {
  const absent = evaluatePayload([{ key: 'toolchain', present: false, bytes: 0 }])
  const row = absent.find((entry) => entry.key === 'toolchain')
  assert.equal(row.present, false)
  assert.equal(row.overBudget, false)
  assert.equal(row.megabytes, 0)

  // 压根没出现在入参里也一样：不许因为缺一条就判红。
  const missing = evaluatePayload([])
  assert.equal(missing.every((entry) => entry.overBudget === false), true)
  assert.equal(missing.length, PAYLOAD_BUDGETS.length)
})

test('报告里能一眼看出总和与"可外移"的那部分', () => {
  const rows = evaluatePayload([
    { key: 'toolchain', present: true, bytes: 181 * MB },
    { key: 'wheelhouse', present: true, bytes: 49 * MB },
    { key: 'dist', present: true, bytes: 40 * MB }
  ])
  const text = formatReport(rows)
  assert.match(text, /sum: 270\.0 MB/)
  assert.match(text, /movable \(toolchain\/wheelhouse\/magpie\): 230\.0 MB/)
  assert.doesNotMatch(text, /OVER/, '这些都是预算内的，不该出现 OVER')
})

test('超预算的那一行带 OVER 标记', () => {
  const rows = evaluatePayload([{ key: 'toolchain', present: true, bytes: 260 * MB }])
  assert.match(formatReport(rows), /OVER[\s\S]*toolchain/)
})

test('会被剔掉的字节先扣掉再判超不超 —— 闸门不能常年为下载残留而红', () => {
  const root = '/repo/apps/desktop'
  const entries = [{ key: 'magpie', present: true, bytes: 147 * MB }]
  const junk = {
    removed: [{ path: path.join(root, 'build', 'magpie', '.magpie-update', 'magpie-cli.exe.new'), bytes: 75 * MB }],
    freedBytes: 75 * MB
  }

  const stripped = applyJunkStrip(entries, junk, path.join(root, 'build'))
  assert.equal(Math.round(stripped[0].bytes / MB), 72)
  assert.equal(stripped[0].strippedBytes, 75 * MB)

  const rows = evaluatePayload(stripped)
  const magpie = rows.find((row) => row.key === 'magpie')
  assert.equal(magpie.overBudget, false, '扣掉残留后 72 MB 不该超 80 MB 预算')
})

test('扣减只认 build 下的路径，别把别处的同名目录算进来', () => {
  const entries = [{ key: 'magpie', present: true, bytes: 100 * MB }]
  const junk = {
    removed: [{ path: path.join('/somewhere', 'else', '.magpie-update', 'x.new'), bytes: 90 * MB }],
    freedBytes: 90 * MB
  }
  const stripped = applyJunkStrip(entries, junk, path.join('/repo', 'build'))
  assert.equal(stripped[0].bytes, 100 * MB)
})

