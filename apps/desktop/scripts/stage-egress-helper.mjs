"use strict"

/**
 * 把提权侧车的三个 .ps1 摆进随包资源目录 `build/egress/`，打包前跑。
 *
 * 为什么要有这一步：`build/egress/` 是 electron-builder 的 extraResources 来源
 * （`from: "build/egress", to: "egress"` → 装完落在 `<安装>\resources\egress\`），
 * 而 `build/` 整个目录被 .gitignore 挡着——它是暂存区，里头那 61MB 的 mihomo.exe 和
 * 两张地理数据表本来也不该进 git。
 *
 * 但这些脚本必须进 git：它们是源码，且 installer.nsh 在装完那一刻就要
 * `$INSTDIR\resources\egress\register-helper-task.ps1` 存在，脚本不在包里，安装器那一步
 * 就静默失败（ExecWait 失败不打断安装），结果是一台「装好了但 TUN 永远用不了」的机器，
 * 而且从外面完全看不出来。
 *
 * 所以脚本的真身在 `apps/desktop/egress-helper/`（受版本管理），打包前复制到这里。
 * 顺手核对同目录的 mihomo 四件套——缺件时宁可打不出包，也不要打出一个缺引擎的安装器。
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync } from "fs"
import { join, relative, resolve } from "path"

const DESKTOP_ROOT = resolve(import.meta.dirname, "..")
const REPO_ROOT = resolve(DESKTOP_ROOT, "..", "..")

const SRC_DIR = join(DESKTOP_ROOT, "egress-helper")
const OUT_DIR = join(DESKTOP_ROOT, "build", "egress")

/** 这三个是安装/卸载/启动的入口，缺一个安装器就断一环。 */
const REQUIRED_SCRIPTS = ["egress-helper.ps1", "register-helper-task.ps1", "unregister-helper-task.ps1"]

/** 引擎本体与数据；只警告不拦——不装 egress 的构建（比如只跑 UI 测试）没它们也合法。 */
const ENGINE_FILES = ["bin/mihomo.exe", "bin/wintun.dll", "GeoSite.dat", "geoip.metadb"]

function fail(message) {
  console.error(`[stage-egress-helper] ${message}`)
  process.exit(1)
}

if (!existsSync(SRC_DIR)) {
  fail(`找不到脚本源目录 ${relative(REPO_ROOT, SRC_DIR)} —— 侧车脚本是源码，必须留在仓库里。`)
}

mkdirSync(OUT_DIR, { recursive: true })

const staged = []
for (const name of REQUIRED_SCRIPTS) {
  const from = join(SRC_DIR, name)
  if (!existsSync(from)) fail(`${relative(REPO_ROOT, from)} 不存在，安装器会缺这一步。`)
  const to = join(OUT_DIR, name)
  copyFileSync(from, to)
  staged.push(name)
}

// 源目录里若有别的 .ps1（以后加的辅助脚本）一并带上：漏一个的表现同样是「装完没反应」。
for (const name of readdirSync(SRC_DIR)) {
  if (!name.endsWith(".ps1") || REQUIRED_SCRIPTS.includes(name)) continue
  copyFileSync(join(SRC_DIR, name), join(OUT_DIR, name))
  staged.push(name)
}

const missing = ENGINE_FILES.filter((name) => !existsSync(join(OUT_DIR, name)))
if (missing.length > 0) {
  console.warn(
    `[stage-egress-helper] 注意：${relative(REPO_ROOT, OUT_DIR)} 里缺 ${missing.join("、")}。` +
      "由这一步打出来的安装包会没有引擎本体（TUN / 系统代理都用不了），只适合做界面验收。",
  )
}

console.log(`[stage-egress-helper] 已就位 ${staged.join("、")} → ${relative(REPO_ROOT, OUT_DIR)}`)
