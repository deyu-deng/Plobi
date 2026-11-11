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
 *   开发态   显式 `PLOBI_DESKTOP_DATA_DIR` → `PLOBI_DEV_HOME` → `PLOBI_HOME` → `%LOCALAPPDATA%\Plobi-Dev`
 *
 * 2.1（2026-11-08）开发态自愈：`PLOBI_HOME` 与安装记录**同址**时判为污染并忽略。
 *     不能只靠"安装器以后别写了"——已经装出去的那一份安装器还会写，开发态得自己
 *     站得起来。专用钥匙 `PLOBI_DEV_HOME` 同理受这一条约束（指到装机目录就作废）。
 *
 * 两处刻意的取舍：
 *
 * 1. **装机版不看 `PLOBI_HOME`。** 装机版是玩家从开始菜单点开的那个，它继承
 *    的是登录会话的环境，里面躺着的是开发机的门牌。装机版只认「安装时选定的
 *    那一格」与「它自己的默认格」，这样 `setx` 再怎么写都污染不到它。
 * 2. **开发态从 `plobi` 挪到 `Plobi-Dev`。** 老目录留给装机版之外的历史数据，
 *    开发态从此在一格新地方从零长，两边同时开着也抢不到同一把锁。
 */

import fs from 'node:fs'
import path from 'node:path'

/** 「这一格现在摸得到吗」——注入进来，`resolveDataRoot` 自己仍然不碰盘。 */
export type DirProbe = (dir: string) => boolean

/** 数据目录从哪一档判定出来的——写进日志，出问题时一眼看出被谁带偏了。 */
export type DataRootSource =
  | 'explicit-env'
  | 'install-record'
  | 'default-packaged'
  | 'dev-home-env'
  | 'home-env'
  | 'default-dev'
  /** 候选路径与安装记录同址 ⇒ 判定为安装器写进来的污染，已忽略并回落开发默认。 */
  | 'default-dev-polluted'
  /**
   * 首选的几档**摸不到**（盘符不在、目录不可写，典型是安装时选了移动硬盘
   * `E:\` 而这台机器上现在没有 E 盘），于是退到下一档。后缀说明"是哪一档
   * 被跳过了"，`:根本落点` 仍是 `root` 本身——启动日志里一眼看出为什么
   * 没落在用户当初选的那一格。
   */
  | 'explicit-env-unusable'
  | 'install-record-unusable'
  | 'dev-home-env-unusable'
  | 'home-env-unusable'
  /** 连默认格都摸不到：仍返回它（不静默改道），让上层把话说清楚。 */
  | 'default-packaged-unusable'
  | 'default-dev-unusable'

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
  /**
   * `PLOBI_DEV_HOME` —— 开发态数据根的**专用**指定，优先级高于 `PLOBI_HOME`。
   *
   * 为什么还要多一格：`PLOBI_HOME` 是公共门牌，装机版安装器历史上会往
   * `HKCU\Environment` 写它（指向安装目录）。开发态若只认它，等于把命交给
   * 别人的安装器。有了这一格，开发态可以只认自己那把钥匙，装机版更新搅不动它。
   * 装机版分支永远不看它（见 resolveDataRoot）。
   */
  devHomeEnv?: string | null
  /** 安装时用户选的数据目录（注册表 `HKCU\Software\Plobi\DataDir`，或安装目录下的记录文件）。 */
  installRecordedDir?: string | null
  /** `%LOCALAPPDATA%`；缺省时退到 `home`。 */
  localAppData?: string | null
  /** 用户主目录，最后兜底。 */
  home: string
  /**
   * 某一格现在摸不摸得到（盘符不在 / 目录不可写）。**不传 = 全部当作摸得到**，
   * 于是既有调用点与既有单测的语义一点不变；只有进程启动那一路会传真探针
   * （:func:`directoryUsable`）。
   *
   * 为什么必须有这一档：安装时把数据目录选在移动硬盘或副盘（`E:\PlobiData`），
   * 换台机器/盘没插时旧行为会拿这一格去首启 `mkdir`，当场 ENOENT 把启动掐死，
   * 而正确做法是退回默认格继续跑（并让日志说出退到了哪一档）。
   */
  isDirUsable?: DirProbe
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
 * 两个路径是不是同一格。
 *
 * Windows 上大小写不敏感、尾分隔符可有可无（`D:\Data\AppData\Plobi\` 与
 * `d:\data\appdata\plobi` 是同一个地方）——比较时两边都收敛掉，避免同一个目录
 * 因为写法不同而漏判成"没被污染"。
 */
function sameDir(a: string, b: string): boolean {
  const left = normalize(clean(a)).replace(/[\\/]+$/, '').toLowerCase()
  const right = normalize(clean(b)).replace(/[\\/]+$/, '').toLowerCase()
  return Boolean(left) && left === right
}

/**
 * 判定数据目录。
 *
 * 纯函数：不碰文件系统、不读环境变量，输入全从参数来，便于单测覆盖四条分支。
 */
export function resolveDataRoot(input: DataRootInput): DataRootResolution {
  const defaultBase = normalize(clean(input.localAppData) || input.home)
  const devDefault = defaultBase ? path.join(defaultBase, 'Plobi-Dev') : 'Plobi-Dev'
  const packagedDefault = defaultBase ? path.join(defaultBase, 'Plobi') : 'Plobi'

  // 没注入探针 ⇒ 每一格都算摸得到（纯函数语义不变）。注入之后，摸不到的那一档
  // 就让位给下一档，并把"谁让的位"写进 source。
  const usable = (dir: string): boolean => (input.isDirUsable ? input.isDirUsable(dir) : true)

  // ① 显式注入的那一格，两份都认——调试、CI、以及安装器首次拉起都走这条。
  const explicit = normalize(clean(input.explicitDir))
  if (explicit) {
    if (usable(explicit)) {
      return { root: explicit, source: 'explicit-env' }
    }
    // 显式那一格摸不到时**不换道**：它是有人拿参数指定的（CI、快捷方式、安装器
    // 首启），默默改到 `%LOCALAPPDATA%` 会把产物写到没人看的地方。照样返回。
    return { root: explicit, source: 'explicit-env-unusable' }
  }

  if (input.isPackaged) {
    // ② 装机版只认安装时选的那一格，然后才是自己的默认格。
    const recorded = normalize(clean(input.installRecordedDir))
    if (recorded) {
      if (usable(recorded)) {
        return { root: recorded, source: 'install-record' }
      }
      // 装载时选的那块盘不在了：退默认格，但别让这件事静默——source 说清楚。
      return {
        root: packagedDefault,
        source: usable(packagedDefault) ? 'install-record-unusable' : 'default-packaged-unusable'
      }
    }
    return {
      root: packagedDefault,
      source: usable(packagedDefault) ? 'default-packaged' : 'default-packaged-unusable'
    }
  }

  // ③ 开发态：`PLOBI_DEV_HOME` 优先，其次 `PLOBI_HOME`，最后 `Plobi-Dev`。
  const recorded = normalize(clean(input.installRecordedDir))
  const fromDev = normalize(clean(input.devHomeEnv))
  const fromHome = normalize(clean(input.homeEnv))

  // 污染识别：候选格与安装记录同址 ⇒ 那是装机版的目录，不是开发态的钥匙。
  // 成因很具体：装机版安装器把 `PLOBI_HOME` 写进 `HKCU\Environment` 指着安装目录，
  // 开发态下一次登录就继承到它 ⇒ 开发态直接读写玩家数据。这里认出来就不认它。
  const polluted = (candidate: string): boolean => Boolean(recorded) && sameDir(candidate, recorded)

  const devDefaultUsable = (): DataRootSource =>
    usable(devDefault) ? 'default-dev' : 'default-dev-unusable'

  if (fromDev) {
    if (polluted(fromDev)) {
      return { root: devDefault, source: 'default-dev-polluted' }
    }
    if (usable(fromDev)) {
      return { root: fromDev, source: 'dev-home-env' }
    }
    // 这台机器上开发态的门牌指向一块不存在的盘（换机、盘拔了）：退回默认格，
    // 而不是拿一格不存在的地方去首启 mkdir。
    if (fromHome && !polluted(fromHome) && usable(fromHome)) {
      return { root: fromHome, source: 'dev-home-env-unusable' }
    }
    return {
      root: devDefault,
      source: usable(devDefault) ? 'dev-home-env-unusable' : devDefaultUsable()
    }
  }

  if (fromHome) {
    if (polluted(fromHome)) {
      return { root: devDefault, source: 'default-dev-polluted' }
    }
    if (usable(fromHome)) {
      return { root: fromHome, source: 'home-env' }
    }
    return {
      root: devDefault,
      source: usable(devDefault) ? 'home-env-unusable' : devDefaultUsable()
    }
  }
  return { root: devDefault, source: devDefaultUsable() }
}

/**
 * 默认探针：这一格**现在**能不能拿来放数据。
 *
 * 判据只有两条，都是"能不能把它建出来"的最小前提：
 *
 *   1. 已经存在 ⇒ 必须是目录（同名文件占位也算摸不到）；
 *   2. 不存在 ⇒ 往上找最近的已存在祖先，它必须是目录。Windows 上盘符不存在时
 *      `path.dirname` 会收敛到 `E:\` 自身不再前进，这里就是那个"到此为止"。
 *
 * 只读不建：探测阶段就去 `mkdir` 会在用户拒绝的那一档留下空壳目录，下次启动
 * 又被它认成"摸得到"。创建留给真正要用这格的代码，失败由那一层的 try/catch 说话。
 */
export function directoryUsable(dir: string): boolean {
  const target = normalize(clean(dir))
  if (!target) {
    return false
  }
  try {
    if (fs.existsSync(target)) {
      return fs.statSync(target).isDirectory()
    }
  } catch {
    return false
  }

  let probe = path.dirname(target)
  for (;;) {
    try {
      if (fs.existsSync(probe)) {
        return fs.statSync(probe).isDirectory()
      }
    } catch {
      return false
    }
    const parent = path.dirname(probe)
    if (parent === probe) {
      // 走到根都不是目录 ⇒ 这块盘/这个根不存在。
      return false
    }
    probe = parent
  }
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
