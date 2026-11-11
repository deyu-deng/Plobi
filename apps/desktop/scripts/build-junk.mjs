/**
 * 打包垃圾剔除：`build/` 里有哪些东西不该跟着进包。
 *
 * 单独成模块的原因很实际——体积账本（`payload-size.mjs`）要在不打包的时候也算得出
 * "打包会瘦多少"，而 `before-pack.mjs` 一旦被 import 就会连带把 electron-builder
 * 拖进来。账本只需要这一条判据，不该为它装一整套打包环境。
 *
 * `build/` 是收集出来的待打包树，不是源码，但里面的东西常常是**运行期产物**：最典型
 * 的是 `.magpie-update/` —— magpie 侧车自更新时先落 `magpie-cli.exe.new`（75 MB），
 * 校验通过才换名。构建机上只要有人更新过 magpie，这个半截文件就躺在 `build/magpie/`
 * 里跟着进包：装机版白胖 75 MB，包里还多一个"不是正在用的那个二进制"。
 *
 * 反过来，这个目录在运行期**不能随便删**（那是别人正在下载的文件），所以清理只在
 * 打包钩子里触发，日常脚本一律不碰。
 */

import fs, { existsSync, rmSync } from 'node:fs'
import path from 'node:path'

export const BUILD_JUNK_NAMES = ['.magpie-update', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache']
export const BUILD_JUNK_FILES = ['.DS_Store', 'Thumbs.db']
export const BUILD_JUNK_EXTENSIONS = ['.pyc', '.pyo']

function isJunk(entry) {
  if (entry.isDirectory()) return BUILD_JUNK_NAMES.includes(entry.name)
  if (!entry.isFile()) return false
  if (BUILD_JUNK_FILES.includes(entry.name)) return true
  return BUILD_JUNK_EXTENSIONS.some((ext) => entry.name.endsWith(ext))
}

export function sizeOf(target) {
  let stat
  try {
    stat = fs.statSync(target)
  } catch {
    return 0
  }
  if (stat.isFile()) return stat.size

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
        /* 正被写走就算了 */
      }
    }
  }
  return total
}

/**
 * 清掉 `build/` 下的打包垃圾，返回清掉的清单与字节数。
 *
 * `dryRun: true` 只统计不动手。删除失败不抛 —— 一个被占用的 `.pyc` 不值得让整个
 * 构建挂掉，日志里说清楚就行。
 */
export function scrubBuildJunk(buildDir, options = {}) {
  const dryRun = options.dryRun === true
  const removed = []
  if (!buildDir || typeof buildDir !== 'string' || !existsSync(buildDir)) {
    return { removed, freedBytes: 0 }
  }

  const stack = [buildDir]
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
      if (!isJunk(entry)) {
        if (entry.isDirectory()) stack.push(full)
        continue
      }

      const bytes = sizeOf(full)
      if (!dryRun) {
        try {
          rmSync(full, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
        } catch (err) {
          console.warn(`[before-pack] could not strip ${full} (${err.message}); it will ship as-is`)
          continue
        }
      }
      removed.push({ path: full, bytes })
    }
  }

  return { removed, freedBytes: removed.reduce((sum, item) => sum + item.bytes, 0) }
}
