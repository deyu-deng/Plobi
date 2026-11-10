/**
 * Windows 上那套「自己带着的工具链」：随包走 git/bash 与 ripgrep，首启不下载。
 *
 * 2026-11-07 用户判词：「为什么我们的软件下载起来这么复杂，和别人的一点都不一样」
 * ——别人的安装包把运行时一起带走，我们的让**首启**去下载。于是：
 *
 *   ① 装机器时把 PortableGit（git + bash）与 ripgrep 放进包里；
 *   ② 装的时候优先用包里那份，没有才下载；
 *   ③ 桌面壳与后端都要能在**任何**地方找到 bash（用户把 Git 装在 D 盘也算数）。
 *
 * 这一档盯第①③段的接线（能被真跑的都真跑；解 200 MB 的归档不适合进测试 ⇒ 那段
 * 由本机实跑与 CI 的构建验）。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { buildPinArgs, resolveBundledBackend } from './bootstrap-runner'

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const REPO_ROOT = path.resolve(HERE, '..', '..', '..')

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-toolchain-test-'))
}

test('包里那份工具链会被认出来并交给安装脚本', () => {
  const root = tmpRoot()
  try {
    fs.mkdirSync(path.join(root, 'backend', 'scripts'), { recursive: true })
    fs.writeFileSync(path.join(root, 'backend', 'scripts', 'install.ps1'), '# install')
    fs.mkdirSync(path.join(root, 'toolchain'), { recursive: true })

    const bundle = resolveBundledBackend(root)
    assert.ok(bundle, 'backend + toolchain 都在 ⇒ 认得出来')
    assert.equal(bundle?.toolchain, path.join(root, 'toolchain'))

    const args = buildPinArgs({ commit: 'a'.repeat(40), branch: 'main' }, { bundle })
    assert.ok(args.includes('-ToolchainDir'), `args=${args.join(' ')}`)
    assert.equal(args[args.indexOf('-ToolchainDir') + 1], path.join(root, 'toolchain'))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('没有工具链时不提那个参数（mac/linux 走原来的路）', () => {
  const root = tmpRoot()
  try {
    fs.mkdirSync(path.join(root, 'backend', 'scripts'), { recursive: true })
    fs.writeFileSync(path.join(root, 'backend', 'scripts', 'install.ps1'), '# install')

    const bundle = resolveBundledBackend(root)
    assert.equal(bundle?.toolchain, null)

    const args = buildPinArgs({ commit: 'a'.repeat(40), branch: 'main' }, { bundle })
    assert.ok(!args.includes('-ToolchainDir'), `args=${args.join(' ')}`)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('桌面壳找 bash 的那几处，与后端那几处一样宽', () => {
  const main = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf-8')
  const local = fs.readFileSync(path.join(REPO_ROOT, 'tools', 'environments', 'local.py'), 'utf-8')

  for (const needle of [
    'PLOBI_GIT_BASH_PATH',
    "'git', 'bin', 'bash.exe'",
    "findOnPath('git')"
  ]) {
    assert.ok(main.includes(needle), `桌面壳的查找少了一处：${needle}`)
  }

  for (const needle of ['PLOBI_GIT_BASH_PATH', '"bin", "bash.exe"', 'shutil.which("git")']) {
    assert.ok(local.includes(needle), `后端的查找少了一处：${needle}`)
  }
})

test('收集脚本钉死版本，git/ripgrep 只在 Windows 上收', () => {
  const script = fs.readFileSync(
    path.join(HERE, '..', 'scripts', 'collect-toolchain-bundle.mjs'),
    'utf-8'
  )

  // 脚本已改成按平台返回资产表（platformAssets），不再是开头一句
  // `if (process.platform !== 'win32') return` —— 这条断言原先钉的是那个写法，
  // 重构后仍在钉旧实现，于是别的平台的腿接进来时它就开始红。
  assert.ok(script.includes("process.platform === 'win32'"), 'Windows 必须单独一支')
  assert.ok(script.includes('PortableGit-'), 'PortableGit 的版本要钉死')
  assert.ok(script.includes('ripgrep-'), 'ripgrep 的版本要钉死')
  assert.ok(script.includes('github.com/git-for-windows/git/releases/download'), '下载地址要写死')
})
