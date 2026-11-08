'use strict'
/**
 * stage-egress-assets.mjs — put the routing engine's files into the bundle.
 *
 * Why this exists: `network.egress` can supervise mihomo, but on a machine that only ever
 * installed the app there is no mihomo, no `GeoSite.dat`, no `geoip.metadb` and no TUN driver
 * — and an engine started without the geo data prints "start download" and never binds, while
 * that download needs the egress the engine was supposed to provide (纸 §28). The user's 10-12
 * ruling was 随包全带; this is the build side of it, so nobody has to run a script after
 * installing (口径 32).
 *
 * Two deliberate limits:
 *  · **The files are not in git.** Code is a public product repo (裁定 134), so a 59 MB binary
 *    and its data files are staged from `PLOBI_EGRESS_SRC` at pack time, never committed.
 *  · **A missing source is loud, never silent, and never fatal to the build.** It writes a
 *    `MISSING.txt` marker into the bundle and the runtime says the same thing in
 *    `plobi network status` / the panel — "this build shipped without the engine" is a reading,
 *    not a guess someone has to discover by watching a hung engine.
 */
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'

export const EGRESS_ASSETS = [
  { name: 'mihomo.exe', subdir: 'bin' },
  { name: 'GeoSite.dat', subdir: '' },
  { name: 'geoip.metadb', subdir: '' },
  // 11-01: TUN 的驱动。引擎按名字加载 `wintun.dll`（exe 目录 → System32 → 当前目录），
  // 随包那一半落在 bin/ 里与 mihomo.exe 同目录；缺它守卫会过、引擎会起、网卡不会建。
  { name: 'wintun.dll', subdir: 'bin' }
]

function walk(dir, depth = 0, out = []) {
  if (depth > 4) {
    return out
  }

  let entries = []

  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return out
  }

  for (const entry of entries) {
    const full = path.join(dir, entry.name)

    if (entry.isDirectory()) {
      walk(full, depth + 1, out)
    } else {
      out.push(full)
    }
  }

  return out
}

/**
 * Decide what to copy where. `srcDir` may be any layout (a portable tool dir, the engine's own
 * workdir, a prepared vendor dir) — we look for the file names, up to four levels down, and take
 * the newest match so a refreshed copy wins over a stale one.
 */
export function planStaging({ srcDir, destDir, files = EGRESS_ASSETS }) {
  const plan = { copy: [], missing: [], srcDir: srcDir || '', destDir }

  if (!srcDir) {
    return { ...plan, missing: files.map((file) => file.name) }
  }

  const found = walk(srcDir)

  for (const file of files) {
    const matches = found.filter((full) => path.basename(full).toLowerCase() === file.name.toLowerCase())

    if (!matches.length) {
      plan.missing.push(file.name)
      continue
    }

    const source = matches.map((full) => ({ full, mtime: fs.statSync(full).mtimeMs })).sort((a, b) => b.mtime - a.mtime)[0].full

    plan.copy.push({ from: source, to: path.join(destDir, file.subdir, file.name), subdir: file.subdir, name: file.name })
  }

  return plan
}

function digest(file) {
  const hash = crypto.createHash('sha256')

  hash.update(fs.readFileSync(file))

  return hash.digest('hex')
}

export default async function stageEgressAssets({ srcDir = process.env.PLOBI_EGRESS_SRC || '', destDir, files = EGRESS_ASSETS, log = console.log } = {}) {
  const plan = planStaging({ srcDir, destDir, files })

  for (const item of plan.copy) {
    fs.mkdirSync(path.dirname(item.to), { recursive: true })
    fs.copyFileSync(item.from, item.to)
    log(`[egress-assets] 随包：${item.name} ← ${item.from} (${fs.statSync(item.to).size} 字节, sha256 ${digest(item.to).slice(0, 12)}…)`)
  }

  const marker = path.join(destDir, 'MISSING.txt')

  fs.rmSync(marker, { force: true })

  // 这张纸描述的必须是**目录的最终状态**，不是这一次调用的结果。before-pack 在多 target /
  // 多 arch 打包里会被调不止一次：某一次拿不到源，plan.missing 就是四件全缺，于是上一次已经
  // 投放好的 mihomo.exe 旁边被塞进一张「四件全缺」的 MISSING.txt —— 包里东西是全的，纸却说
  // 没有，谁看包谁被误导（11-08 那个包就是这样，实测路径 resources/egress）。
  // 按 destDir 现况重算缺什么，缺才写，齐了就只留下四件。
  const absent = files
    .filter((file) => !fs.existsSync(path.join(destDir, file.subdir, file.name)))
    .map((file) => file.name)

  if (absent.length) {
    fs.mkdirSync(destDir, { recursive: true })
    fs.writeFileSync(
      marker,
      [
        'Plobi shipped WITHOUT these routing-engine files, so `network.egress` can compile a',
        'config but cannot start an engine on this machine until they are placed under',
        `$PLOBI_HOME/egress/ (see Docs: 随包分发 / 裁定 134 — they are deliberately not in git).`,
        'Build-side fix: set PLOBI_EGRESS_SRC to a directory containing them and re-pack.',
        '',
        ...absent.map((name) => `missing: ${name}`),
        ''
      ].join('\n'),
      'utf8'
    )
    log(`[egress-assets] 这版没带：${absent.join('、')}（已在包内留 MISSING.txt，运行期会照实报）`)
  }

  return plan
}
