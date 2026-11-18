import assert from 'node:assert/strict'
import test from 'node:test'

import { createChangeOnlyLogger, formatDiagnosticRows } from './desktop-log'

test('诊断行的标签与说明相同时只写一遍', () => {
  assert.equal(
    formatDiagnosticRows([{ label: '暂时无法获取出口状态', toolTip: '暂时无法获取出口状态' }]),
    '暂时无法获取出口状态'
  )
})

test('诊断行的标签与说明不同时写成「标签 :: 说明」', () => {
  assert.equal(
    formatDiagnosticRows([{ label: '出口', toolTip: '127.0.0.1:7890' }]),
    '出口 :: 127.0.0.1:7890'
  )
})

test('诊断行没有说明时只留标签', () => {
  assert.equal(formatDiagnosticRows([{ label: '出口' }]), '出口')
  assert.equal(formatDiagnosticRows([{ label: '出口', toolTip: '  ' }]), '出口')
})

test('多行诊断行用「 | 」隔开', () => {
  assert.equal(
    formatDiagnosticRows([
      { label: '出口', toolTip: '7890' },
      { label: '暂时无法获取出口状态', toolTip: '暂时无法获取出口状态' }
    ]),
    '出口 :: 7890 | 暂时无法获取出口状态'
  )
})

test('同一句话只落一笔：状态没变就不出声', () => {
  const written: string[] = []
  const logger = createChangeOnlyLogger(line => written.push(line))

  assert.equal(logger.emit('quota', '[sidecar] 目录读不到'), true)
  assert.equal(logger.emit('quota', '[sidecar] 目录读不到'), false)
  assert.equal(logger.emit('quota', '[sidecar] 目录读不到'), false)
  assert.equal(logger.emit('quota', '[sidecar] 别的错'), true)

  assert.deepEqual(written, ['[sidecar] 目录读不到', '[sidecar] 别的错'])
})

test('擦掉那把钥匙之后，下一次同一句话照旧出声', () => {
  const written: string[] = []
  const logger = createChangeOnlyLogger(line => written.push(line))

  logger.emit('quota', '[sidecar] 目录读不到')
  logger.clear('quota')
  logger.emit('quota', '[sidecar] 目录读不到')

  assert.deepEqual(written, ['[sidecar] 目录读不到', '[sidecar] 目录读不到'])
})

test('不同的钥匙互不相干', () => {
  const written: string[] = []
  const logger = createChangeOnlyLogger(line => written.push(line))

  logger.emit('a', '第一句')
  logger.emit('b', '第二句')
  logger.emit('a', '第一句')

  assert.deepEqual(written, ['第一句', '第二句'])
})
