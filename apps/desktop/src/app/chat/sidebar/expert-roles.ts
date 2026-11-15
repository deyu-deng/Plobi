/**
 * 临时专家的**分类名册**（用户 11-01：左栏「专家」抽屉里放的是角色名册，不是会话流水）。
 *
 * 三件事分得很清（2026-11-15 收口）：
 * - **有哪些类、什么顺序** → `@/shared/expert-trades` 的 `EXPERT_TRADES`（镜像后端
 *   `plobi/delegation/expert_roles.py` 的 `TRADES`，由 `expert-trades.test.ts` 对表）；
 * - **这一行长什么样** → 本文件的 `LOOK`（图标是视觉，不进后端），中文名走 i18n `labelKey`；
 * - **这一类派出去干什么** → 后端 `DUTIES` / `TOOLSETS`，前端一个字都不抄。
 *
 * 以前这三件事散在三处、加一类要改三个文件，漏一处就是一行永远点不开的空行；
 * 现在 `LOOK` 是 `Record<ExpertRoleKey, …>`，漏一类**编译就红**，不必等人点开才发现。
 */
import { EXPERT_TRADES, type ExpertRoleKey } from '@/shared/expert-trades'

import type { ExpertRun } from './expert-runs'

/** 角色键只在一处定义（`shared/expert-trades.ts`）：主区卡片与侧栏名册必须同一套。 */
export type { ExpertRoleKey }

export interface ExpertRoleSpec {
  /** Codicon 名。 */
  icon: string
  key: ExpertRoleKey
  /** `console` 文案里那句人话的名词。 */
  labelKey: 'expertRoleBrowser' | 'expertRoleComputer' | 'expertRoleFile' | 'expertRoleSoftware'
}

/** 每类的长相。键取自 `ExpertRoleKey`，缺一类 TypeScript 直接报错——这就是那道牙。 */
const LOOK: Record<ExpertRoleKey, { icon: string; labelKey: ExpertRoleSpec['labelKey'] }> = {
  file: { icon: 'file', labelKey: 'expertRoleFile' },
  browser: { icon: 'globe', labelKey: 'expertRoleBrowser' },
  software: { icon: 'terminal', labelKey: 'expertRoleSoftware' },
  computer: { icon: 'device-desktop', labelKey: 'expertRoleComputer' }
}

/** 顺序只由 `EXPERT_TRADES` 决定，这里不重排。 */
export const EXPERT_ROLES: readonly ExpertRoleSpec[] = EXPERT_TRADES.map(key => ({
  key,
  icon: LOOK[key].icon,
  labelKey: LOOK[key].labelKey
}))

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
