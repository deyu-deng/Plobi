#!/usr/bin/env node
/**
 * 安装包的体重账本。
 *
 * 为什么要单挑一个脚本：`Plobi-0.3.1-win-x64.exe` 是 421 MB，其中不动的部分是
 * `toolchain`（181 MB 的源码压缩包：cpython / node / ffmpeg / PortableGit / uv /
 * ripgrep）和 `wheelhouse`（49 MB 的离线依赖轮子）。这两块每次发版都要**整包
 * 重下一遍**，而它们在自己的节奏上更新（magpie 侧车已经是这个思路了）。所以
 * 体重这件事得先看得见，才谈得上拆不拆：
 *
 *   - 平时 `node scripts/payload-size.mjs` 打一张构成表；
 *   - 打包链里带 `--strict` 跑，超预算就非零退出（谁把包喂胖了当场拦下）。
 *
 * 预算给的是**宽松**值：这里的作用是"涨了要有交代"，不是把包锁死在某个数字上。
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { scrubBuildJunk } from './build-junk.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const APP_ROOT = path.resolve(HERE, '..')
const MB = 1024 * 1024

/**
 * 各项预算（MB）。`movable` 记的是"这块能不能从主包里挪出去"——拆包后自动更新
 * 只拉应用本体，这块就不再跟着走。
 */
export const PAYLOAD_BUDGETS = [
  { key: 'toolchain', label: 'toolchain（cpython/node/ffmpeg/git/uv/rg 源码包）', budgetMb: 200, movable: true },
  { key: 'wheelhouse', label: 'wheelhouse（离线依赖轮子）', budgetMb: 60, movable: true },
  { key: 'backend', label: 'backend（Python 后端源码 + chatlog 28 MB）', budgetMb: 85, movable: false },
  { key: 'magpie', label: 'magpie（侧车二进制；不含 .magpie-update）', budgetMb: 80, movable: true },
  { key: 'dist', label: 'dist（渲染层产物）', budgetMb: 120, movable: false },
  { key: 'egress', label: 'egress（引擎三件套：bin + Geo 库）', budgetMb: 80, movable: false },
  { key: 'quota_hub', label: 'quota_hub', budgetMb: 20, movable: false }
]

/** 这些目录/文件不进预算表但会被打进包，列出来只为了"总数说得通"。 */
const EXTRA_DIRS = ['assets', 'public']

function dirSize(target) {
  let total = 0
  const stack = [target]
  while (stack.length > 0) {
    const current = stack.pop()
    let entries
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) {
        stack.push(full)
        continue
      }
      try {
        total += fs.statSync(full).size
      } catch {
        /* 打包中被拿走的文件不算错 */
      }
    }
  }
  return total
}

/**
 * 把"某一项的字节数"折成判断结果。纯函数，方便单测——真正的取数在 `measure()`。
 *
 * `present: false` 表示这一项没构建过（比如只跑了 `vite build` 没跑
 * `collect:*`）。缺项**不算超预算**：没做的活儿不该被报成做坏了。
 */
export function evaluatePayload(entries, budgets = PAYLOAD_BUDGETS) {
  const byKey = new Map(entries.map((entry) => [entry.key, entry]))
  return budgets.map((budget) => {
    const entry = byKey.get(budget.key)
    const present = Boolean(entry && entry.present)
    const bytes = present ? entry.bytes : 0
    const megabytes = bytes / MB
    return {
      key: budget.key,
      label: budget.label,
      movable: budget.movable,
      present,
      bytes,
      megabytes,
      budgetMb: budget.budgetMb,
      overBudget: present && megabytes > budget.budgetMb
    }
  })
}

/**
 * `build/` 里那些"打包时会被剔掉、但现在占着地方"的东西。列出来是为了解释
 * "为什么 `Get-ChildItem` 数是 147 MB，而包里只该有 71 MB"。
 */
export function measureJunk(appRoot = APP_ROOT) {
  const { removed, freedBytes } = scrubBuildJunk(path.join(appRoot, 'build'), { dryRun: true })
  return { removed, freedBytes }
}

/**
 * 把"打包时会剔掉的字节"从对应项目里扣掉，得到真实进包的体积。
 *
 * 不扣的话账本会一直为 `.magpie-update` 那 75 MB 报 OVER，而它根本不会进包 ——
 * 闸门一旦常红就等于没有闸门。扣掉之后 OVER 只代表"真胖了"。
 */
export function applyJunkStrip(entries, junk, buildDir = path.join(APP_ROOT, 'build')) {
  if (!junk || junk.removed.length === 0) return entries

  const strippedByKey = new Map()
  for (const item of junk.removed) {
    const relative = path.relative(buildDir, item.path)
    if (relative.startsWith('..')) continue
    const key = relative.split(path.sep)[0]
    strippedByKey.set(key, (strippedByKey.get(key) || 0) + item.bytes)
  }

  return entries.map((entry) => {
    const stripped = strippedByKey.get(entry.key) || 0
    if (stripped === 0) return entry
    return { ...entry, bytes: Math.max(0, entry.bytes - stripped), strippedBytes: stripped }
  })
}

export function measure(appRoot = APP_ROOT) {
  const keys = ['toolchain', 'wheelhouse', 'backend', 'magpie', 'dist', 'egress', 'quota_hub']
  const entries = keys.map((key) => {
    // dist 在应用根下，其余都在 build/ 里（electron-builder 的 extraResources 源）。
    const candidates = [path.join(appRoot, 'build', key), path.join(appRoot, key)]
    const found = candidates.find((candidate) => fs.existsSync(candidate))
    return { key, present: Boolean(found), bytes: found ? dirSize(found) : 0 }
  })

  const extras = EXTRA_DIRS.map((key) => {
    const target = path.join(appRoot, key)
    return { key, present: fs.existsSync(target), bytes: fs.existsSync(target) ? dirSize(target) : 0 }
  })

  return { entries, extras }
}

/**
 * 已知的待拆项：写在这里是为了让"包为什么这么大"有个能查的答案，而不是每次
 * 重新推一遍。
 */
export const KNOWN_HEAVY_HITTERS = [
  'toolchain 与 wheelhouse 是**离线优先**的选择：包里自带 python/node/ffmpeg/git 与全部轮子，',
  '装机不需要联网、不需要用户装 Python。代价是主包 400 MB 级别，且自动更新每次都得整包重下。',
  '要瘦下去只有一条路：把这两块拆成独立资产（版本号 + 哈希 + 首次启动/download 时按需取、',
  '已装且版本未变就复用），主包只留应用本体。这需要真打一次包来验证（安装器、首启引导、',
  '离线降级三条链路都要重过），不适合在"不打包"的窗口里改。'
]

function fmt(mb) {
  return `${mb.toFixed(1)} MB`
}

export function formatReport(rows, junk) {
  const lines = []
  const present = rows.filter((row) => row.present)
  const total = present.reduce((sum, row) => sum + row.bytes, 0)
  const movable = present.filter((row) => row.movable).reduce((sum, row) => sum + row.bytes, 0)

  lines.push('payload composition (extraResources + dist)')
  for (const row of rows) {
    const mark = !row.present ? '  n/a ' : row.overBudget ? ' OVER ' : '  ok  '
    lines.push(
      `${mark}${row.label.padEnd(52)} ${fmt(row.megabytes).padStart(10)}  / budget ${String(row.budgetMb).padStart(4)} MB${
        row.movable ? '  (movable)' : ''
      }`
    )
  }
  if (junk && junk.removed.length > 0) {
    lines.push('')
    lines.push(`pack-time strip: ${fmt(junk.freedBytes / MB)} 会被 before-pack 剔掉（不进包）`)
    for (const item of junk.removed) {
      lines.push(`  - ${path.relative(APP_ROOT, item.path)}  ${fmt(item.bytes / MB)}`)
    }
    lines.push(`effective total after strip: ${fmt((total - junk.freedBytes) / MB)}`)
  }
  lines.push('')
  lines.push(`sum: ${fmt(total / MB)}   of which movable (toolchain/wheelhouse/magpie): ${fmt(movable / MB)}`)
  return lines.join('\n')
}

function main(argv) {
  const strict = argv.includes('--strict')
  const { entries, extras } = measure()
  const junk = measureJunk()
  const rows = evaluatePayload(applyJunkStrip([...entries, ...extras], junk))
  console.log(formatReport(rows, junk))

  const over = rows.filter((row) => row.overBudget)
  if (over.length > 0) {
    console.log('')
    console.log('over budget:')
    for (const row of over) {
      console.log(`  - ${row.key}: ${fmt(row.megabytes)} > ${row.budgetMb} MB`)
    }
    console.log('')
    console.log('如果这次是有意的（新加资源/升级运行库），改 PAYLOAD_BUDGETS 并说明原因；')
    console.log('如果没打算喂胖，那这笔账要算到引它进来的那个提交上。')
    if (strict) {
      process.exitCode = 1
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  main(process.argv.slice(2))
}
