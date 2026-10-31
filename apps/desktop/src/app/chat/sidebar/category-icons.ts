import type { AgentCategory } from './agent-groups'

/**
 * 五个 Agent 类型各自一枚语义清楚的图标（用户 10-31：不要那个虚线方块）。
 * 后端新加的类型没有登记就用 `symbol-misc` 兜底——不会因此掉一行。
 */
const CATEGORY_ICON: Record<string, string> = {
  projects: 'folder',
  courses: 'mortar-board',
  butler: 'account',
  events: 'calendar',
  research: 'microscope'
}

export function categoryIcon(category: AgentCategory): string {
  return CATEGORY_ICON[category] ?? 'symbol-misc'
}
