/**
 * 裁定 38/39 (WP-AGENT-MOUTH) 终态（2026-10-19 用户裁定收口）：「会话」与
 * 「profile」都不再是产品概念 —— 左栏在**任何壳**里都是纯 Agents 列表，
 * 会话搜索 / 置顶 / 最近列表 / Agent 底下的会话抽屉 / 底部 ProfileRail
 * 全部撤出（原「主线仍是会话底座」的过渡保留已废止）。
 *
 * Pure functions so the rules are unit-testable without spinning up the
 * whole sidebar tree.
 */

export interface SidebarNavLevel {
  l1: boolean
  l2Id: null | string
}

export function sidebarNavForLevel<T>(nav: readonly T[], level: SidebarNavLevel): T[] {
  // `new-session` 已不是任何壳的产品（裁定 38/39 终态）：留着这条过滤是给
  // 还带着旧 nav 数组的调用方兜底，别让遗留项从这条缝里爬回来。
  //
  // 2026-10-20 用户裁定 C 起，`skills`（Capabilities）在 **L2 里也要有**：那一页
  // 展示的是「这个 Agent 关掉了哪些」，开关只写它自己的名册行。此前它被这条过滤
  // 挡在 L2 之外，是因为能力面板当时挂在右栏（那一块已按同一裁定删掉）。
  if (level.l2Id) {
    return nav.filter((item: any) => item.id !== 'new-session')
  }

  return nav.filter((item: any) => item.id !== 'new-session')
}

/**
 * 裁定 38/39: inside the secretary shell an Agent row is one mouth — it never
 * expands into a session drawer and never shows a session count. Drives
 * `AgentCategoryGroup`'s `hideSessions` at the (only) call site.
 */
export function hidesAgentSessions(level: null | string | { l2: string } | undefined): boolean {
  return Boolean(level)
}
