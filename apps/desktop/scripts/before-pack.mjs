'use strict'
/**
 * before-pack.mjs — electron-builder beforePack hook.
 *
 * Two responsibilities:
 *
 * 1. Removes any stale unpacked app directory (`appOutDir`) before
 *    electron-builder stages the Electron binaries into it.
 *
 * WHY THIS EXISTS
 * ---------------
 * electron-builder's final packaging step copies the stock `electron`
 * binary into `release/<platform>-unpacked/` and then renames it to the
 * product name (`Plobi`). If a PREVIOUS `npm run pack` was interrupted
 * (Ctrl-C, OOM kill, crash, full disk) the unpacked directory is left in a
 * corrupted partial state: it keeps the already-renamed `LICENSE.electron.txt`
 * and the Chromium payload (.pak/.so/icudtl.dat/chrome-sandbox) but is MISSING
 * the `electron` binary itself.
 *
 * On the next run, electron-builder sees the destination directory already
 * populated, skips re-copying the binary it thinks is present, then tries to
 * rename a `electron` file that no longer exists. The build dies with:
 *
 *   ENOENT: no such file or directory, rename
 *   '.../release/linux-unpacked/electron' -> '.../release/linux-unpacked/Plobi'
 *
 * This is a hard failure with no obvious cause for the user — `plobi desktop`
 * just prints "Desktop GUI build failed" and the only fix is to manually
 * `rm -rf` the release directory, which a normal user has no way to know.
 *
 * The packaging step is not idempotent across an interrupted run, so we make
 * it idempotent ourselves: wipe the target unpacked directory up front so
 * electron-builder always stages into a clean tree. This is safe — the
 * directory is a pure build artifact that electron-builder fully recreates
 * on every pack; nothing else depends on its prior contents.
 *
 * Cross-platform: the same partial-state trap exists on macOS
 * (the mac-unpacked Plobi.app bundle) and Windows (win-unpacked), so we
 * clean whatever `appOutDir` electron-builder hands us regardless of platform.
 *
 * Best-effort: a cleanup failure must never mask the real build. We log and
 * resolve rather than throw — worst case electron-builder hits the original
 * ENOENT, which is no worse than not having this hook at all.
 *
 * 2. Re-stages node-pty's native files for the ACTUAL target platform/arch
 *    of this pack. `npm run build` already staged node-pty once for the
 *    host machine (see scripts/stage-native-deps.mjs), which is correct for
 *    single-arch builds matching the host. But electron-builder can target
 *    a different arch than the host (cross-build), or pack multiple archs
 *    from one `npm run build` (e.g. `dist:mac` => x64 + arm64). Only this
 *    hook knows the real per-target arch, via `context.arch` /
 *    `context.electronPlatformName` — so it re-stages on top of whatever
 *    `npm run build` left behind, per target, right before files are read
 *    for packing.
 *
 * electron-builder passes a context with:
 *   - appOutDir:            the unpacked app directory about to be staged
 *   - electronPlatformName: 'win32' | 'darwin' | 'linux'
 *   - arch:                 Arch enum (0=ia32, 1=x64, 2=armv7l, 3=arm64, 4=universal)
 */
import { existsSync, readdirSync, rmSync } from 'node:fs'
import { Arch } from 'electron-builder'
import path from 'node:path'
import { stageNodePty } from './stage-native-deps.mjs'
import { scrubBuildJunk } from './build-junk.mjs'
import stageEgressAssets, { EGRESS_ASSETS } from './stage-egress-assets.mjs'

export function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string') {
    return false
  }
  if (!existsSync(appOutDir)) {
    return false
  }
  // Recursive + force so a half-written tree (read-only bits, partial files)
  // can't block the wipe. retry/maxRetries rides out transient EBUSY on
  // Windows where an AV/indexer may briefly hold a handle.
  rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  return true
}

/**
 * 路由引擎源目录：`PLOBI_EGRESS_SRC` 优先，其次仓内 `.egress-src/ship`。
 *
 * 裁定 134 让这几个二进制不进 git，于是「不写进代码」很容易被读成「构建时也不用带」——
 * 结果 before-pack 只传了 destDir，stage 拿到的 srcDir 永远是空串，包里只剩 MISSING.txt。
 * 这里给出显式回退；真找不到时返回空串，让 stage 照实报 missing，不假装带上了。
 */
export function resolveEgressSrc(projectDir) {
  const explicit = process.env.PLOBI_EGRESS_SRC
  if (explicit) {
    // 显式给了就是显式给了：不再往下猜。指错了要立刻看得出来，而不是被后面的
    // 回退链悄悄救回来 —— 那会让人以为 PLOBI_EGRESS_SRC 生效了。
    return existsSync(explicit) ? explicit : ''
  }

  // 11-12: 不再写死 `../..`。以前只有一个层级假设（apps/desktop → 仓根），一旦
  // projectDir 落到别的层（monorepo 里改了 appDir、从 release/ 或另一个 checkout
  // 里打包、electron-builder 换了 config 入口），回退链就整体落空 —— 四个候选全都
  // 不存在，stage 拿到空串，包里只剩一张 MISSING.txt，而构建照样成功。11-11 19:00
  // 那个安装包就是这么来的（hook 已是修好的版本，包里却四项全缺）。
  // 现在改成从 projectDir 逐级向上找，层数不再是正确性的前提。
  for (const dir of egressCandidates(projectDir)) {
    // 「目录在」不等于「东西在」。一个空的 `.egress-src`（建过、后来又清过，或者别人
    // 机器上只留了壳）会让回退链在这里停下，把后面的候选全部挡掉，结果与完全没找到
    // 一模一样地缺四件套。所以命中条件是：这两层里确实认得到随包资产的名字。
    if (existsSync(dir) && holdsEgressAssets(dir)) return dir
  }
  return ''
}

/** 从 `projectDir` 起逐级向上，列出所有会被当作源目录的路径（近的在前）。 */
export function egressCandidates(projectDir) {
  const roots = []
  let cursor = path.resolve(projectDir)
  for (let depth = 0; depth < 6; depth += 1) {
    roots.push(cursor)
    const parent = path.dirname(cursor)
    if (parent === cursor) break
    cursor = parent
  }
  const candidates = []
  for (const root of roots) {
    candidates.push(path.join(root, '.egress-src', 'ship'))
    candidates.push(path.join(root, '.egress-src'))
  }
  return candidates
}

/** 给缺源告警用：把候选路径压成一行可读的样子，不用人再去翻代码推层级。 */
function describeEgressCandidates(projectDir) {
  const explicit = process.env.PLOBI_EGRESS_SRC
  if (explicit) return [`PLOBI_EGRESS_SRC=${explicit}${existsSync(explicit) ? '' : '（不存在）'}`]
  return egressCandidates(projectDir)
}

/** `dir` 里（最多两层）能否看到随包资产的真名 —— 只认名字，不读 61MB 的内容。 */
function holdsEgressAssets(dir) {
  const wanted = new Set(EGRESS_ASSETS.map((asset) => asset.name.toLowerCase()))
  const stack = [{ dir, depth: 0 }]
  while (stack.length > 0) {
    const { dir: current, depth } = stack.pop()
    let entries
    try {
      entries = readdirSync(current, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (entry.isFile() && wanted.has(entry.name.toLowerCase())) return true
      if (entry.isDirectory() && depth < 2) {
        stack.push({ dir: path.join(current, entry.name), depth: depth + 1 })
      }
    }
  }
  return false
}

export default async function beforePack(context) {
  const appOutDir = context && context.appOutDir
  const projectDir =
    (context && (context.appDir || context.projectDir)) ||
    (context && context.packager && context.packager.projectDir) ||
    process.cwd()

  // 打包垃圾（下载中的侧车、pyc 缓存）先剔掉，别让构建机上跑过的运行期副产物进包。
  // 放在最前面：后面几步 stage 进来的东西都还在，清理只看 build/。
  try {
    const { removed, freedBytes } = scrubBuildJunk(path.join(projectDir, 'build'))
    if (removed.length > 0) {
      const names = removed.map((item) => path.relative(projectDir, item.path)).join('、')
      console.log(`[before-pack] 剔掉打包垃圾 ${names}（省 ${(freedBytes / 1048576).toFixed(1)} MB）`)
    }
  } catch (err) {
    console.warn(`[before-pack] build junk scrub did not run (${err.message}); continuing`)
  }

  // 随包分发（用户 10-12 拍「随包全带」，裁定 134 规定不进仓）：从 PLOBI_EGRESS_SRC 投放三件套。
  // 缺源不炸构建——但会在包里留 MISSING.txt，运行期的 `plobi network status` 照实说"这版没带"，
  // 而不是让人从一台挂住的引擎现场倒推。
  try {
    const srcDir = resolveEgressSrc(projectDir)
    const plan = await stageEgressAssets({ srcDir, destDir: path.join(projectDir, 'build', 'egress') })

    if (!plan.missing.length) {
      console.log(`[before-pack] 随包带上路由引擎：${plan.copy.map((item) => item.name).join('、')}`)
    } else {
      // 缺源时把「从哪儿找起、找到哪一层」一起打出来。11-11 那次包里四项全缺，日志里
      // 只有「源：未找到」一句，等于要人从零复现一遍 hook 的推导过程 —— 这行成本最低。
      console.warn(
        `[before-pack] 随包仍缺路由引擎：${plan.missing.join('、')}\n` +
          `  找过的起点：${projectDir}\n` +
          `  找过的地方：${describeEgressCandidates(projectDir).join('、')}\n` +
          `  想指定源：PLOBI_EGRESS_SRC=<含 ${EGRESS_ASSETS.map((asset) => asset.name).join('/')} 的目录>`
      )
    }
  } catch (err) {
    console.warn(`[before-pack] egress asset staging did not run (${err.message}); the app will report the engine as not shipped`)
  }
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`)
    }
  } catch (err) {
    // Never fail the build over cleanup; surface why so a genuinely stuck
    // directory (permissions, mount) is still diagnosable.
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`)
  }

  try {
    const platform = context && context.electronPlatformName
    const archName = context && typeof context.arch === 'number' ? Arch[context.arch] : undefined
    if (platform && archName) {
      if (archName === 'universal') {
        console.warn(
          '[before-pack] target arch is "universal" — node-pty has no universal prebuild; ' +
            'staged binary will be whichever single-arch copy npm run build left behind. ' +
            'lipo-merge x64/arm64 .node files manually if you need a true universal build.'
        )
      } else {
        await stageNodePty({ platform, arch: archName })
        console.log(`[before-pack] re-staged node-pty for target ${platform}-${archName}`)
      }
    }
  } catch (err) {
    // This one SHOULD fail the build — a missing/wrong native binary for the
    // target arch means a broken package shipped to users, which is worse
    // than a build that fails loudly here.
    throw new Error(`[before-pack] failed to stage node-pty for this target: ${err.message}`)
  }
}