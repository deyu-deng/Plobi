/**
 * 「第一启动 bootstrap 已完成」marker（`.plobi-bootstrap-complete`）的判定。
 *
 * 这段逻辑原来与 IO 一起埋在 main.ts 的 isBootstrapComplete() 里 —— 只有 Electron
 * 主进程能跑到，没有任何单测。于是 0.3.0 的 bundled 安装写出 `pinnedCommit: null`
 * 的 marker、校验又从不认它这件事，一路带到用户机器上才暴露：那台机器每次启动都
 * 跳过「已装可用」这一档，转去 PATH 里找 `plobi`，找不到就重跑整段 bootstrap。
 *
 * 现在把「marker 算不算完成」抽成纯函数：本模块只判定、不写盘，补写由主进程做。
 *
 * **第二刀（`desktopVersion` 比对）**：老判定只看「marker schema 合法 + 运行时能
 * import plobi_cli」，从不看这台机器上的运行时副本属于哪个桌面版本。于是 0.3.0
 * 装出的 `~/Plobi/plobi-agent` 一路被认作「已 bootstrap」，升级到 0.3.4 后仍跑
 * 0.3.0 的开发态代码（现场：进程 19418 的 pyproject 还写着 0.3.0 /
 * pinnedCommit 6248aaf）。判定必须把 marker 里的 `desktopVersion` 跟
 * `app.getVersion()` 比一次：不一致 ⇒ 需要重建，不是「已完成」。
 */

export const BOOTSTRAP_MARKER_SCHEMA_VERSION = 1

/** pinnedCommit 的 7 字符下限：短于此长度视为被截断 / 伪造的垃圾值。 */
export const MIN_PINNED_COMMIT_LENGTH = 7

export interface BootstrapMarkerVerdict {
  /** 可以认为 bootstrap 已完成：后续启动跳过安装流程。 */
  complete: boolean
  /** marker 里的 pinnedCommit 是否是一份合法的 attestation。 */
  pinnedCommitValid: boolean
  /** 需要把 marker 补写成合法形态（老版本写坏的那一批）。 */
  needsRepair: boolean
  /**
   * 需要**重跑安装**（不只是补写 marker）：运行时副本与当前桌面版本不匹配。
   * 与 `needsRepair` 互斥——补写 marker 修不好一个版本过时的运行时。
   */
  needsRebuild: boolean
  /** 直接进日志的一句话原因。 */
  reason: string
}

function describeCommit(raw: unknown): string {
  if (raw === null) {
    return 'null'
  }

  if (raw === undefined) {
    return 'undefined'
  }

  if (typeof raw === 'string') {
    return JSON.stringify(raw)
  }

  return `<${typeof raw}>`
}

function describeVersion(raw: unknown): string {
  if (typeof raw === 'string' && raw.trim() !== '') {
    return raw.trim()
  }

  return describeCommit(raw)
}

/**
 * 判定 marker。
 *
 * @param marker          `readBootstrapMarker()` 读到的原始 JSON（可能为 null / 非对象）
 * @param runtimeUsable   当前安装是否真的能跑起来（源码树 + venv + 能 import plobi_cli）
 * @param desktopVersion  当前桌面版本（`app.getVersion()`）。留空 = 不比对版本，
 *                        只为老测试与「拿不到版本」的调用方保底。
 */
export function evaluateBootstrapMarker(
  marker: unknown,
  runtimeUsable: boolean,
  desktopVersion?: string
): BootstrapMarkerVerdict {
  if (!marker || typeof marker !== 'object') {
    return {
      complete: false,
      pinnedCommitValid: false,
      needsRepair: false,
      needsRebuild: false,
      reason: 'marker missing or not an object'
    }
  }

  const record = marker as Record<string, unknown>

  if (record.schemaVersion !== BOOTSTRAP_MARKER_SCHEMA_VERSION) {
    return {
      complete: false,
      pinnedCommitValid: false,
      needsRepair: false,
      needsRebuild: false,
      reason: `schemaVersion ${String(record.schemaVersion)} != ${BOOTSTRAP_MARKER_SCHEMA_VERSION}`
    }
  }

  const rawPinned = record.pinnedCommit

  const pinnedCommitValid =
    typeof rawPinned === 'string' && rawPinned.length >= MIN_PINNED_COMMIT_LENGTH

  // 版本比对排在「运行时能不能跑」之前：一个能 import 的 0.3.0 运行时**照样**
  // 是错的运行时。老 marker（0.3.3 及以前）根本没写 desktopVersion，那是「凭证
  // 不完整」，同样无法证明它配的是当前版本 ⇒ 一并判需要重建。
  const expected = (desktopVersion || '').trim()

  if (expected !== '') {
    const recorded = typeof record.desktopVersion === 'string' ? record.desktopVersion.trim() : ''

    if (recorded !== expected) {
      return {
        complete: false,
        pinnedCommitValid,
        needsRepair: false,
        needsRebuild: true,
        reason: `runtime belongs to desktopVersion ${describeVersion(record.desktopVersion)}, current is ${expected}`
      }
    }
  }

  if (!runtimeUsable) {
    // marker 写坏 + 运行时也起不来 ⇒ 该重跑安装。这时候补写 marker 是自欺欺人。
    return {
      complete: false,
      pinnedCommitValid,
      needsRepair: false,
      needsRebuild: false,
      reason: 'install runtime is not usable'
    }
  }

  if (pinnedCommitValid) {
    return {
      complete: true,
      pinnedCommitValid,
      needsRepair: false,
      needsRebuild: false,
      reason: 'marker valid and runtime usable'
    }
  }

  // 运行时已经证明可用（源码树 + venv + 能 import plobi_cli）。marker 只是
  // 「install.ps1 在这台机器上跑完过」的凭证，`pinnedCommit` 写成 null（0.3.0 /
  // 0.3.1 的 bundled 装机就是这样）属于**凭证字段损坏**，不是安装损坏。
  // 补写成合成值即可，不能让这些机器每次启动都重跑一遍安装。
  return {
    complete: true,
    pinnedCommitValid,
    needsRepair: true,
    needsRebuild: false,
    reason: `legacy marker: pinnedCommit was ${describeCommit(rawPinned)}`
  }
}
