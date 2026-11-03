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

import type { ExternalSession } from './external-sessions'
import { VENDOR_ORDER, vendorLabel } from './external-sessions'

/**
 * 台账里那一族回执字段（`reply`/`reply_status`/`reply_to`）。`OffloadRound` 由别的
 * 认领方持有，这里只做**结构扩展**，不往共享类型档里加行。
 */
export type OffloadRoundReply = {
  reply?: string
  reply_status?: string
  reply_to?: string
}

export type OffloadRoundWithReply = OffloadRound & OffloadRoundReply

export type { OffloadRound }

export type OffloadGroup = { rounds: OffloadRoundWithReply[], target: string }

const UNKNOWN_TARGET = 'unknown'

/** 按外包对象分组；组内新的在前，组间按名字稳定排序（侧栏不许跳来跳去）。 */
export function groupRoundsByTarget(rounds: readonly OffloadRoundWithReply[]): OffloadGroup[] {
  const byTarget = new Map<string, OffloadRoundWithReply[]>()

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

/**
 * 外包那一格的一张表（用户 11-01）：
 * **L2 自己派出去的那几轮** 和 **你在那家软件里的会话** 是同一件事的两面，都是「外包给谁」，
 * 所以按对象（那家软件）分组只分一次，两路条目挂在同一个组下面。10-28 之前它们是上下
 * 两格（「外包给」+「外部会话」），同一个对象在屏上出现两回，读起来像两个概念。
 */
export type OutsourcedGroup = {
  /** 那家软件的名字（`vendorLabel` 翻过，屏上直接可用）。 */
  label: string
  rounds: OffloadRoundWithReply[]
  sessions: ExternalSession[]
  vendor: string
}

const UNKNOWN_VENDOR = 'unknown'

/** ISO 串的比较只用来排序，读不出就是 0（不编一个时间）。 */
function timeOf(iso?: string): number {
  const parsed = Date.parse(iso ?? '')

  return Number.isFinite(parsed) ? parsed : 0
}

function vendorRank(vendor: string): number {
  const index = VENDOR_ORDER.indexOf(vendor)

  return index < 0 ? VENDOR_ORDER.length : index
}

export function groupOutsourced(
  rounds: readonly OffloadRoundWithReply[],
  sessions: readonly ExternalSession[]
): OutsourcedGroup[] {
  const byVendor = new Map<string, OutsourcedGroup>()

  const slot = (raw: string): OutsourcedGroup => {
    const vendor = (raw || '').trim() || UNKNOWN_VENDOR
    const found = byVendor.get(vendor)

    if (found) {
      return found
    }

    const created: OutsourcedGroup = { label: vendorLabel(vendor), rounds: [], sessions: [], vendor }
    byVendor.set(vendor, created)

    return created
  }

  for (const round of rounds) {
    slot(round.agent ?? '').rounds.push(round)
  }

  for (const session of sessions) {
    slot(session.vendor ?? '').sessions.push(session)
  }

  return [...byVendor.values()]
    .map(group => ({
      ...group,
      rounds: [...group.rounds].sort((a, b) => timeOf(b.ts) - timeOf(a.ts)),
      sessions: [...group.sessions].sort((a, b) => timeOf(b.updated_at) - timeOf(a.updated_at)),
    }))
    .sort((a, b) => vendorRank(a.vendor) - vendorRank(b.vendor) || a.label.localeCompare(b.label))
}

export type OutsourcedItem =
  | { at: number, kind: 'round', round: OffloadRoundWithReply }
  | { at: number, kind: 'session', session: ExternalSession }

/** 一组里两路条目按时间倒序并排——同一组里它们平等，不按来源分先后。 */
export function outsourcedItems(group: OutsourcedGroup): OutsourcedItem[] {
  return [
    ...group.rounds.map(round => ({ at: timeOf(round.ts), kind: 'round' as const, round })),
    ...group.sessions.map(session => ({ at: timeOf(session.updated_at), kind: 'session' as const, session })),
  ].sort((a, b) => b.at - a.at)
}

export type JumpReason = '' | 'no-artifact'

export type JumpPlan = {
  kind: 'none' | 'reveal' | 'resume'
  reason: JumpReason
  resume: null | { agent: string, sessionId: string }
  url: string
}

/** 本地路径 → 主进程认得的 `file://`。裸路径会被 `new URL()` 判非法，点了就没反应。 */
export function fileUrlFor(localPath: string): string {
  const slashed = localPath.trim().replace(/\\/g, '/').replace(/^\/+/, '')
  const encoded = slashed.split('/').map(part => encodeURIComponent(part)).join('/')

  return `file:///${encoded.replace(/^([A-Za-z])%3A/i, '$1:')}`
}

/** 这一轮有没有带回外部软件自己那条会话的标识。**空串是一个读数**（通道没带出来），不是失败。 */
export function externalHandleOf(round: OffloadRound): string {
  return (round.external_session ?? '').trim()
}

/**
 * 「跳进它自己那条会话」那一支**有没有资格亮**。契约第 4 条：台账行有非空 `external_session`
 * 才准可点，**空串永远不许渲染成可点**。这一支是那条支路的唯一判据，别在组件里另判一遍。
 */
export function handleJumpEligible(round: OffloadRound): boolean {
  return externalHandleOf(round) !== ''
}

/** 这一轮留在盘上的第一个产物（台账里的 `artifacts[]` 就是它的证据）。 */
export function artifactPathOf(round: OffloadRound): string {
  return (round.artifacts ?? []).map(item => (item?.path ?? '').trim()).find(Boolean) ?? ''
}

/**
 * 一行点下去到哪儿，**只在这一处判**：
 *
 * 1. `resume` —— 台账带回会话号 ⇒ 交给 `POST /api/offload/resume`（白名单、argv、**以及工作区**都在后端，
 *    那边才是"这台怎么起那家 Agent、那一段活在哪干"的唯一答案）。没有对象名就跳不了，不猜。
 * 2. `reveal` —— 只有产物 ⇒ 给主进程一个 `file://`，它打不开时自己退成"在文件夹里选中"。
 * 3. `none` —— 两样都没有，`reason` 是机器可读的，不许伪装成可点。
 */
export function jumpTargetFor(round: OffloadRound): JumpPlan {
  const sessionId = externalHandleOf(round)
  const agent = (round.agent ?? '').trim()

  if (handleJumpEligible(round) && agent) {
    return { kind: 'resume', reason: '', resume: { agent, sessionId }, url: '' }
  }

  const path = artifactPathOf(round)

  if (path) {
    return { kind: 'reveal', reason: '', resume: null, url: fileUrlFor(path) }
  }

  return { kind: 'none', reason: 'no-artifact', resume: null, url: '' }
}

export type RoundFacts = {
  costStatus: string
  costUsd: null | number
  credits: null | number
  durationSeconds: null | number
  permissionFlag: string
}

/**
 * 一行上的四个读数（给了谁在组头上，这一段是什么与目录由渲染层取 `brief`/`cwd`）。
 *
 * 金额算不出必须是 `null` 而不是 0（R-073）；真报价是 0 也照样是 0。
 * 有的厂商按积分记账（Qoder：美元报 0、积分报 3.78），两种都收，显示时美元优先。
 */
export function roundFacts(round: OffloadRound): RoundFacts {
  const usage = (round.usage ?? {}) as Record<string, unknown>
  const rawCost = usage.cost_usd ?? usage.total_cost_usd
  const rawCredits = usage.total_credits ?? usage.credits
  const seconds = Number(round.duration_seconds)

  return {
    costStatus: (round.cost_status ?? '').trim(),
    costUsd: typeof rawCost === 'number' && Number.isFinite(rawCost) ? rawCost : null,
    credits: typeof rawCredits === 'number' && Number.isFinite(rawCredits) ? rawCredits : null,
    durationSeconds: Number.isFinite(seconds) && seconds > 0 ? seconds : null,
    permissionFlag: (round.permission_flag ?? '').trim(),
  }
}
