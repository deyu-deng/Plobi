/**
 * 临时专家的**四类角色**（用户 11-01：左栏「专家」抽屉里放的是角色名册，不是会话流水）。
 *
 * 这份表镜像后端 `plobi/delegation/expert_roles.py` 的 `ROLES` / `ORDER`：后端决定
 * 派出去的子智能体是什么角色、拿哪一层工具面；前端只决定这一行长什么样。
 * 顺序与后端 `ORDER` 一致，别在这里重排——两边的次序不一致时，用户会以为少了一类。
 *
 * 新增一类角色要两处一起改；只改这里会得到一行永远点不开的空行。
 */
import type { ExpertRun } from './expert-runs'

export type ExpertRoleKey = 'browser' | 'computer' | 'file' | 'software'

export interface ExpertRoleSpec {
  /** Codicon 名。 */
  icon: string
  key: ExpertRoleKey
  /** `console` 文案里那句人话的名词。 */
  labelKey: 'expertRoleBrowser' | 'expertRoleComputer' | 'expertRoleFile' | 'expertRoleSoftware'
}

export const EXPERT_ROLES: readonly ExpertRoleSpec[] = [
  { icon: 'file', key: 'file', labelKey: 'expertRoleFile' },
  { icon: 'globe', key: 'browser', labelKey: 'expertRoleBrowser' },
  { icon: 'terminal', key: 'software', labelKey: 'expertRoleSoftware' },
  { icon: 'device-desktop', key: 'computer', labelKey: 'expertRoleComputer' }
]

/** 这一类角色跑过的所有会话（一个角色可以派很多次，每次都是一条子会话）。 */
export function runsOfRole(runs: readonly ExpertRun[], role: ExpertRoleKey): ExpertRun[] {
  return runs.filter(run => (run.role ?? '').trim().toLowerCase() === role)
}

/**
 * 「一个专家一张嘴」（用户 11-01 选定的形态）：点这一行就打开它最近那条会话。
 * 派过多次时取 `started_at` 最大的那条——旧的那几条仍在，只是这一行不再铺成列表。
 */
export function latestRun(runs: readonly ExpertRun[]): ExpertRun | null {
  let best: ExpertRun | null = null

  for (const run of runs) {
    if (!best || (run.started_at ?? 0) > (best.started_at ?? 0)) {
      best = run
    }
  }

  return best
}
