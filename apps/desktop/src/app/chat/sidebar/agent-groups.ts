import type { Agent } from '@/app/console/types'

/**
 * R-012 sidebar agent taxonomy. **The backend registry owns the list of
 * categories** (`plobi/agents/registry.py::AGENT_CATEGORIES`); the client does
 * NOT keep a copy of it — that copy is how a new category silently folded into
 * another column (courses landed in 项目/管家 until the client was patched too).
 *
 * So: a non-empty `category` is rendered as its own group, whatever it is.
 * The only thing left here is *display order*, which is a presentation choice,
 * not a gate. Unknown categories are appended alphabetically.
 */
export type AgentCategory = string

/**
 * Preferred order for the long-standing columns. Not a whitelist.
 *
 * WP-AGENT-CREATE-UPGRADE（用户 10-16）：`courses` 进这里——后端
 * `AGENT_CATEGORIES` 一直有它（裁定 121 二），但创建对话框的选项表用的是这份
 * 偏好顺序，缺了就「选不中」（用户原话「Courses Agent 甚至不可以选中」）。
 */
export const AGENT_CATEGORY_PREFERENCE: readonly AgentCategory[] = [
  'projects',
  'courses',
  'butler',
  'events',
  'research'
]

/**
 * Empty / missing folds into `butler` — legacy rows predate R-012 and are
 * secretary-shaped (that part of R-012 still holds). An *unknown non-empty*
 * value is kept verbatim so a new backend category shows up without a client
 * edit; `i18n` falls back to the raw key when a label is missing.
 */
export function normalizeAgentCategory(raw?: null | string): AgentCategory {
  const key = (raw ?? '').trim().toLowerCase()

  return key || 'butler'
}

/** Known-first (preferred order), then anything new, alphabetically. */
export function orderedCategories(present: Iterable<AgentCategory>): AgentCategory[] {
  const seen = new Set(present)
  const known = AGENT_CATEGORY_PREFERENCE.filter(category => seen.has(category))
  const rest = [...seen].filter(category => !known.includes(category)).sort((a, b) => a.localeCompare(b, 'zh'))

  return [...known, ...rest]
}

export interface AgentCategoryGroup<T> {
  agents: T[]
  category: AgentCategory
}

/**
 * Bucket agents by category. Preferred categories come first (see
 * {@link orderedCategories}), the rest follow alphabetically, and only
 * non-empty groups are returned. Each agent list is sorted by display name.
 */
export function groupAgentsByCategory<T extends Pick<Agent, 'category' | 'name'>>(
  agents: readonly T[]
): Array<AgentCategoryGroup<T>> {
  const buckets = new Map<AgentCategory, T[]>()

  for (const agent of agents) {
    const category = normalizeAgentCategory(agent.category)
    const bucket = buckets.get(category)

    if (bucket) {
      bucket.push(agent)
    } else {
      buckets.set(category, [agent])
    }
  }

  return orderedCategories(buckets.keys()).map(category => ({
    agents: (buckets.get(category) ?? []).slice().sort((a, b) => a.name.localeCompare(b.name, 'zh')),
    category
  }))
}

/** Profiles an agent chats under (used to nest agent-owned sessions). */
export function agentProfileIdSet(agents: readonly Agent[]): Set<string> {
  const out = new Set<string>()

  for (const agent of agents) {
    const profile = (agent.profile ?? '').trim()

    if (profile) {
      out.add(profile)
    }
  }

  return out
}

/**
 * 这一行能不能点进聊天页。
 *
 * 名册里 `profile` 为空的是**数据记录**（`l2_course`：课是数据不是分身，裁定 40），
 * 它没有运行时可绑。以前点它会让客户端替它编一个 profile 名去起后端（`agent.id` 那条
 * 回退），于是每一门课都是一场重启循环。既然点进去只能是一张空壳，行本身就不该可点。
 */
export function isNavigableAgent(agent: Pick<Agent, 'profile'>): boolean {
  return Boolean((agent.profile ?? '').trim())
}

// The desktop controller injects SessionInfo over IPC, and that layer always
// sends an empty `profile` string — so we can't trust the field's truthfulness.
// L1 identity is therefore "default OR master OR blank OR any profile that is
// NOT one of the known agent profiles". An agent's own thread is only excluded
// when it actually carries that agent's profile (which the backend populates,
// not the session-profile IPC fallback).
export function isL1SessionProfile(profile?: null | string, agentProfileSet?: ReadonlySet<string>): boolean {
  const normalized = (profile ?? '').trim().toLowerCase() || 'default'

  if (normalized === 'master') {
    return true
  }

  if (normalized === 'default') {
    return true
  }

  // Empty profile (the common IPC case) is the user's own main thread, not an agent.
  if (!profile || normalized === '') {
    return true
  }

  if (agentProfileSet?.has(normalized)) {
    return false
  }

  // Unknown profile: conservative — keep it out of agent nesting as L1.
  return true
}
