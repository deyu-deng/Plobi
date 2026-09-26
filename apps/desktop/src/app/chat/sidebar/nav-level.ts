/**
 * 裁定 38/39 (WP-AGENT-MOUTH): 「会话」不再是一个产品概念。
 *
 * 秘书壳（L1 总秘书 **和** 每个 L2）里，人面对的是 **Agent**，不是会话 —— 每个
 * Agent 只有一张嘴（一个对话入口）。所以秘书壳里既不出现「新建会话」导航项，
 * 也不渲染会话搜索 / 置顶 / 最近列表 / Agent 底下的会话抽屉。
 *
 * Plobi 主线（没有 `level` 的壳）仍是会话底座，本刀不拆 —— 主线保留
 * `new-session` 与会话列表。
 *
 * Pure functions so the rules are unit-testable without spinning up the
 * whole sidebar tree.
 */

export interface SidebarNavLevel {
  l1: boolean
  l2Id: null | string
}

export function sidebarNavForLevel<T>(nav: readonly T[], level: SidebarNavLevel): T[] {
  if (level.l1) {
    return nav.filter((item: any) => item.id !== 'new-session')
  }

  if (level.l2Id) {
    return nav.filter((item: any) => item.id !== 'new-session' && item.id !== 'skills')
  }

  return [...nav]
}

/**
 * WP-STUDIO (裁定 36.0): the bottom ProfileRail is a Plobi *mainline*
 * affordance ("Switch to profile N", create/rename/delete a profile) — the
 * user's words were 「不像秘书」. The secretary shell hides it, on L1 and on
 * L2 alike; the mainline keeps it. `profile-switcher.tsx` itself stays — the
 * mainline and other shells still render it.
 *
 * Pure predicate so the rule is testable without rendering the sidebar tree.
 */
export function shouldRenderProfileRail(level: null | string | { l2: string } | undefined): boolean {
  return !level
}

/**
 * 裁定 38/39: the secretary shell (any `level`) has no session product — no
 * session search, no pins, no recents, no 「新建会话」 blank state. Only the
 * Plobi mainline (no `level`) shows them.
 */
export function showsSessionSurface(level: null | string | { l2: string } | undefined): boolean {
  return !level
}

/**
 * 裁定 38/39: inside the secretary shell an Agent row is one mouth — it never
 * expands into a session drawer and never shows a session count. Drives
 * `AgentCategoryGroup`'s `hideSessions` at the (only) call site.
 */
export function hidesAgentSessions(level: null | string | { l2: string } | undefined): boolean {
  return Boolean(level)
}
