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

/**
 * 名册 name 集合（2026-11-05 profile 连根拔）。
 *
 * 会话归属键 `sessions.agent_id` 存的就是**名册 name**（= 这一行的 `id`），
 * 不再是家名 / profile 值。左栏要用"这一行在不在名册"筛会话，就该拿 name 建集。
 */
export function agentNameSet(agents: readonly Pick<Agent, 'id'>[]): Set<string> {
  const out = new Set<string>()

  for (const agent of agents) {
    const name = (agent.id ?? '').trim()

    if (name) {
      out.add(name)
    }
  }

  return out
}

/**
 * 这一行能不能点进聊天页。
 *
 * 判据只有后端 R-072 的 `navigable`（`agent_navigability`：点了之后 spawn
 * 不会失败的形状——课程行 heal 成 l2_course 后没有运行时可绑、保留名建不出
 * 家、家还没落地，三者任一都不可点）。11-06 收尾：`profile` 那格已经从响应里
 * 删掉，这里也不再拿它兜底；读不到（老响应）按可点处理，不该因为缺字段把整栏点死。
 */
export function isNavigableAgent(agent: Partial<Pick<Agent, 'navigable'>>): boolean {
  return agent.navigable !== false
}

// 2026-11-05（profile 连根拔 · 桌面批）：L1 判据只有一个 —— 会话行的归属键
// **`agent_id` 为空串**。这是总账 schema 自己的约定（缺口 B：无主行 = L1），
// 不再是"`profile` 标签为空 / 叫 default / 叫 master / 不在家名集合里"那一串猜测。
// 归属非空就一定是某个 Agent 的会话，名册里查不查得到都不影响这条判定——
// 左栏筛"挂到哪一行"另有一道 `agentNameSet` 闸门，两件事不混。
export function isL1SessionProfile(agentId?: null | string): boolean {
  return !(agentId ?? '').trim()
}

/**
 * 会话按归属分桶（2026-11-05 profile 连根拔 · 桌面批）。
 *
 * 桶键是名册 name；三种行分开对待，别混：
 *   - 归属空串 ⇒ **L1**，不进任何 Agent 桶（总秘书那一格由 L1 自己那份清单管）；
 *   - 归属在名册里 ⇒ 进它那一桶；
 *   - 归属非空但名册查不到（名册删过行、旧库被拷进来）⇒ **未归属**，
 *     既不进 Agent 桶，也不折进 `default`。折进 L1 等于把别人的对话记在
 *     总秘书头上：L1 的计数与清单都会多出一票（10-31 那次误归档的同类错）。
 */
const warnedOrphanSessionIds = new Set<string>()

export function bucketSessionsByAgent<T extends { agent_id?: null | string }>(
  sessions: readonly T[],
  agentNames: ReadonlySet<string>
): { byAgent: Map<string, T[]>; unassigned: T[] } {
  const byAgent = new Map<string, T[]>()
  const unassigned: T[] = []

  for (const session of sessions) {
    const agentId = (session.agent_id ?? '').trim()

    if (!agentId) {
      continue // L1：不进 Agent 桶
    }

    if (!agentNames.has(agentId)) {
      unassigned.push(session)
      // 2b-1 风险③：孤儿归属原来是完全静默的，排查时只能靠猜。这里补一条
      // 可见告警，同一个 session id 只打一次（分桶每帧都会跑，不能刷屏）。
      const sessionId = (session as { id?: string }).id ?? '<no-id>'

      if (!warnedOrphanSessionIds.has(sessionId)) {
        warnedOrphanSessionIds.add(sessionId)
        console.warn(
          `[agent-groups] 会话 ${sessionId} 的归属 agent_id="${agentId}" 不在名册里，落到未归属分组`
        )
      }

      continue
    }

    const bucket = byAgent.get(agentId)

    if (bucket) {
      bucket.push(session)
    } else {
      byAgent.set(agentId, [session])
    }
  }

  return { byAgent, unassigned }
}
