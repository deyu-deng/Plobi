/**
 * 「第一启动 bootstrap 已完成」marker（`.plobi-bootstrap-complete`）的判定。
 *
 * 这段逻辑原来与 IO 一起埋在 main.ts 的 isBootstrapComplete() 里 —— 只有 Electron
 * 主进程能跑到，没有任何单测。于是 0.3.0 的 bundled 安装写出 `pinnedCommit: null`
 * 的 marker、校验又从不认它这件事，一路带到用户机器上才暴露：那台机器每次启动都
 * 跳过「已装可用」这一档，转去 PATH 里找 `plobi`，找不到就重跑整段 bootstrap。
 *
 * 现在把「marker 算不算完成」抽成纯函数：本模块只判定、不写盘，补写由主进程做。
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

/**
 * 判定 marker。
 *
 * @param marker         `readBootstrapMarker()` 读到的原始 JSON（可能为 null / 非对象）
 * @param runtimeUsable  当前安装是否真的能跑起来（源码树 + venv + 能 import plobi_cli）
 */
export function evaluateBootstrapMarker(
  marker: unknown,
  runtimeUsable: boolean
): BootstrapMarkerVerdict {
  if (!marker || typeof marker !== 'object') {
    return {
      complete: false,
      pinnedCommitValid: false,
      needsRepair: false,
      reason: 'marker missing or not an object'
    }
  }

  const record = marker as Record<string, unknown>

  if (record.schemaVersion !== BOOTSTRAP_MARKER_SCHEMA_VERSION) {
    return {
      complete: false,
      pinnedCommitValid: false,
      needsRepair: false,
      reason: `schemaVersion ${String(record.schemaVersion)} != ${BOOTSTRAP_MARKER_SCHEMA_VERSION}`
    }
  }

  const rawPinned = record.pinnedCommit

  const pinnedCommitValid =
    typeof rawPinned === 'string' && rawPinned.length >= MIN_PINNED_COMMIT_LENGTH

  if (!runtimeUsable) {
    // marker 写坏 + 运行时也起不来 ⇒ 该重跑安装。这时候补写 marker 是自欺欺人。
    return {
      complete: false,
      pinnedCommitValid,
      needsRepair: false,
      reason: 'install runtime is not usable'
    }
  }

  if (pinnedCommitValid) {
    return {
      complete: true,
      pinnedCommitValid,
      needsRepair: false,
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
    reason: `legacy marker: pinnedCommit was ${describeCommit(rawPinned)}`
  }
}
