/**
 * 一个 L2 把活外包给了谁、点下去能到哪儿（WP-L2-OUTSOURCE-ENTRY 判据 2 + 入口面③）。
 *
 * 这里只放**纯逻辑**（分组、路径降级、读数），渲染层是 `agent-offload-panel.tsx`。
 * 分开是因为"跳不进去就不许假装能跳"这一条必须能被测。
 *
 * 跳转的三级里现在只有中间一级有数据源：
 * 1. 跳到外部软件**自己那一条会话** —— 做不到：通道（`agent/external_offload.py`）
 *    根本不回会话号，台账里就没有这一项，所以这里不渲染可点的假行。
 * 2. 打开这一轮**落盘的东西** —— 有产物就给 `file://`，主进程 `openExternalUrl`
 *    会在系统打不开时退成"在文件夹里选中"。
 * 3. 什么都没有 —— 不可点，并给一个机器可读的原因。
 */

import type { OffloadRound } from '@/types/plobi'

export type { OffloadRound }

export type OffloadGroup = { rounds: OffloadRound[], target: string }

const UNKNOWN_TARGET = 'unknown'

/** 按外包对象分组；组内新的在前，组间按名字稳定排序（侧栏不许跳来跳去）。 */
export function groupRoundsByTarget(rounds: readonly OffloadRound[]): OffloadGroup[] {
  const byTarget = new Map<string, OffloadRound[]>()

  for (const round of rounds) {
    const target = (round.agent ?? '').trim() || UNKNOWN_TARGET
    const list = byTarget.get(target)

    if (list) {
      list.push(round)
    } else {
      byTarget.set(target, [round])
    }
  }

  return [...byTarget.entries()]
    .map(([target, list]) => ({
      rounds: [...list].sort((a, b) => String(b.ts ?? '').localeCompare(String(a.ts ?? ''))),
      target,
    }))
    .sort((a, b) => a.target.localeCompare(b.target))
}

export type JumpReason = '' | 'no-artifact'
export type JumpPlan = { kind: 'none' | 'reveal', reason: JumpReason, url: string }

/** 本地路径 → 主进程认得的 `file://`。裸路径会被 `new URL()` 判非法，点了就没反应。 */
export function fileUrlFor(localPath: string): string {
  const slashed = localPath.trim().replace(/\\/g, '/').replace(/^\/+/, '')
  const encoded = slashed.split('/').map(part => encodeURIComponent(part)).join('/')

  return `file:///${encoded.replace(/^([A-Za-z])%3A/i, '$1:')}`
}

/** 这一轮留在盘上的第一个产物（台账里的 `artifacts[]` 就是它的证据）。 */
export function artifactPathOf(round: OffloadRound): string {
  return (round.artifacts ?? []).map(item => (item?.path ?? '').trim()).find(Boolean) ?? ''
}

export function jumpTargetFor(round: OffloadRound): JumpPlan {
  const path = artifactPathOf(round)

  if (!path) {
    return { kind: 'none', reason: 'no-artifact', url: '' }
  }

  return { kind: 'reveal', reason: '', url: fileUrlFor(path) }
}

export type RoundFacts = {
  costStatus: string
  costUsd: null | number
  durationSeconds: null | number
  permissionFlag: string
}

/**
 * 一行上的四个读数（给了谁在组头上，这一段是什么与目录由渲染层取 `brief`/`cwd`）。
 *
 * 金额算不出必须是 `null` 而不是 0（R-073）；真报价是 0 也照样是 0。
 */
export function roundFacts(round: OffloadRound): RoundFacts {
  const usage = (round.usage ?? {}) as Record<string, unknown>
  const rawCost = usage.cost_usd
  const seconds = Number(round.duration_seconds)

  return {
    costStatus: (round.cost_status ?? '').trim(),
    costUsd: typeof rawCost === 'number' && Number.isFinite(rawCost) ? rawCost : null,
    durationSeconds: Number.isFinite(seconds) && seconds > 0 ? seconds : null,
    permissionFlag: (round.permission_flag ?? '').trim(),
  }
}
