/**
 * WP-DATA-ISOLATION — 装机版与开发态的数据目录判定。
 *
 * 病根：装机版过去会读 `HKCU\Environment` 里那个 `PLOBI_HOME`（那是开发机
 * 用 `setx` 钉的门牌）。于是同一个人装了两份 Plobi，装机版会直接读写开发态
 * 的数据目录；反过来开发态一改，装机版当场跟着变。要的是两条互不相干的路。
 *
 * 定下的规矩：
 *
 *   装机版   显式 `PLOBI_DESKTOP_DATA_DIR` → 安装时选的那一格 → `%LOCALAPPDATA%\Plobi`
 *   开发态   显式 `PLOBI_DESKTOP_DATA_DIR` → `PLOBI_HOME` → `%LOCALAPPDATA%\plobi-dev`
 *
 * 两处刻意的取舍：
 *
 * 1. **装机版不看 `PLOBI_HOME`。** 装机版是玩家从开始菜单点开的那个，它继承
 *    的是登录会话的环境，里面躺着的是开发机的门牌。装机版只认「安装时选定的
 *    那一格」与「它自己的默认格」，这样 `setx` 再怎么写都污染不到它。
 * 2. **开发态从 `plobi` 挪到 `plobi-dev`。** 老目录留给装机版之外的历史数据，
 *    开发态从此在一格新地方从零长，两边同时开着也抢不到同一把锁。
 */

import path from 'node:path'

/** 数据目录从哪一档判定出来的——写进日志，出问题时一眼看出被谁带偏了。 */
export type DataRootSource =
  | 'explicit-env'
  | 'home-env'
  | 'install-record'
  | 'default-packaged'
  | 'default-dev'

export interface DataRootResolution {
  /** 绝对路径，已归一化。 */
  root: string
  source: DataRootSource
}

export interface DataRootInput {
  /** `app.isPackaged`（或 `PLOBI_DESKTOP_IS_PACKAGED`）。 */
  isPackaged: boolean
  /** `PLOBI_DESKTOP_DATA_DIR` —— 安装器 / 快捷方式显式注入的那一格。 */
  explicitDir?: string | null
  /** `PLOBI_HOME` —— 只在开发态算数。 */
  homeEnv?: string | null
  /** 安装时用户选的数据目录（注册表 `HKCU\Software\Plobi\DataDir`，或安装目录下的记录文件）。 */
  installRecordedDir?: string | null
  /** `%LOCALAPPDATA%`；缺省时退到 `home`。 */
  localAppData?: string | null
  /** 用户主目录，最后兜底。 */
  home: string
}

/** 空串 / 纯空白 / 只有一个分隔符，都当没写。 */
function clean(value: string | null | undefined): string {
  const trimmed = String(value ?? '').trim()
  return trimmed ? trimmed : ''
}

/**
 * 把用户给的路径收敛成绝对路径。
 *
 * 安装器页面上手打的 `D:\Data\AppData\Plobi` 可能带尾斜杠、可能是相对路径
 * （某些 NSIS 输入框会这样），这里统一成 `path.resolve` 的形态；失败就返空，
 * 由调用方落到下一档，而不是抛出去把启动流程掐断。
 */
function normalize(value: string): string {
  if (!value) {
    return ''
  }
  try {
    const resolved = path.resolve(value)
    // `path.resolve('')` 会给出 cwd，那不是用户的意思。
    return resolved
  } catch {
    return ''
  }
}

/**
 * 判定数据目录。
 *
 * 纯函数：不碰文件系统、不读环境变量，输入全从参数来，便于单测覆盖四条分支。
 */
export function resolveDataRoot(input: DataRootInput): DataRootResolution {
  const defaultBase = normalize(clean(input.localAppData) || input.home)
  const devDefault = defaultBase ? path.join(defaultBase, 'plobi-dev') : 'plobi-dev'
  const packagedDefault = defaultBase ? path.join(defaultBase, 'Plobi') : 'Plobi'

  // ① 显式注入的那一格，两份都认——调试、CI、以及安装器首次拉起都走这条。
  const explicit = normalize(clean(input.explicitDir))
  if (explicit) {
    return { root: explicit, source: 'explicit-env' }
  }

  if (input.isPackaged) {
    // ② 装机版只认安装时选的那一格，然后才是自己的默认格。
    const recorded = normalize(clean(input.installRecordedDir))
    if (recorded) {
      return { root: recorded, source: 'install-record' }
    }
    return { root: packagedDefault, source: 'default-packaged' }
  }

  // ③ 开发态：`PLOBI_HOME` 说了算（`setx` 钉的那个），否则 `plobi-dev`。
  const fromHome = normalize(clean(input.homeEnv))
  if (fromHome) {
    return { root: fromHome, source: 'home-env' }
  }
  return { root: devDefault, source: 'default-dev' }
}

/**
 * Electron 自己的 `userData`（窗口状态、连接配置、更新记录）也该分家。
 *
 * 不分的后果很具体：`npm run dev` 与开始菜单里那份装机版共用
 * `%APPDATA%\plobi`，单实例锁一撞，先开的那个把后开的顶掉。
 */
export function resolveElectronUserDataRoot(input: {
  isPackaged: boolean
  appDataDir: string
  appName: string
}): string {
  const base = normalize(clean(input.appDataDir) || '.')
  const name = clean(input.appName) || 'plobi'
  return path.join(base, input.isPackaged ? name : `${name}-dev`)
}

/** 数据目录里的固定落点——装机版「完全初始化」就是这一格什么都不存在。 */
export function dataRootLayout(root: string): {
  agentRoot: string
  connectionConfig: string
  updatesConfig: string
  windowState: string
} {
  return {
    agentRoot: path.join(root, 'plobi-agent'),
    connectionConfig: path.join(root, 'connection.json'),
    updatesConfig: path.join(root, 'updates.json'),
    windowState: path.join(root, 'window-state.json')
  }
}
