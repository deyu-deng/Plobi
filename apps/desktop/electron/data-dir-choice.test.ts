/**
 * 「用户自己选的那一格」从装机器一路传到安装脚本——中间不能断。
 *
 * 2026-11-07 实测的事故：用户在安装器里把数据目录选到 D 盘，装完主程序却读到默认格
 * （C 盘）⇒ 数据与 uv 托管的 Python 全落在 C 盘 ⇒ 启动红框。
 *
 * 三段各管一头：① `buildPinArgs` 把「用户选过」变成 `-UserChoseDataDir`；② `main.ts`
 * 把「安装器记下了哪一格」接上①；③ **产品**一个字都不许提这台机器的盘规——那是我们的
 * 规矩，不是别的用户的规矩。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

import { buildPinArgs } from './bootstrap-runner'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const ELECTRON_DIR = HERE

const INSTALL_STAMP = { commit: 'a'.repeat(40), branch: 'main' }

test('用户选过数据目录 ⇒ 安装脚本收到 -UserChoseDataDir；没选过就不带', () => {
  const plain = buildPinArgs(INSTALL_STAMP, {})
  assert.ok(
    !plain.includes('-UserChoseDataDir'),
    `用户没选过就不该带这个开关：${plain.join(' ')}`
  )

  const chosen = buildPinArgs(INSTALL_STAMP, { userChoseDataDir: true })
  assert.ok(
    chosen.includes('-UserChoseDataDir'),
    `用户选过 ⇒ 那个开关必须传下去：${chosen.join(' ')}`
  )
})

test('主进程把「安装器记下的那一格」接上这个开关', () => {
  const main = fs.readFileSync(path.join(ELECTRON_DIR, 'main.ts'), 'utf-8')

  assert.ok(
    main.includes('userChoseDataDir: Boolean(readInstallerRecordedDataDir())'),
    'main.ts 必须把安装器记下的那一格接上 userChoseDataDir'
  )
})

test('产品源码一个字都不许提这台机器的盘规', () => {
  const forbidden = ['_POLICY', 'directory-policy']

  for (const entry of fs.readdirSync(ELECTRON_DIR)) {
    if (!entry.endsWith('.ts') || entry.endsWith('.test.ts')) {
      continue
    }
    const source = fs.readFileSync(path.join(ELECTRON_DIR, entry), 'utf-8')
    for (const needle of forbidden) {
      assert.ok(
        !source.includes(needle),
        `${entry} 里出现了机器盘规的字样（${needle}）——盘规是我们的规矩，不是产品的`
      )
    }
  }
})
