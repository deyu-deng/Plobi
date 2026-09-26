import type { AgentCreateRequest } from '../../console/types'
import { slug } from '@/lib/sanitize'

/**
 * R-012 sidebar taxonomy groups. The order is the order the new-agent
 * dialog renders the category <select> options in.
 */
export const AGENT_CATEGORY_VALUES = ['projects', 'butler', 'events', 'research'] as const

export type AgentCategory = (typeof AGENT_CATEGORY_VALUES)[number]

/** Default category for a human-created agent in the new-agent dialog. */
export const DEFAULT_AGENT_CATEGORY: AgentCategory = 'projects'

/**
 * Registry id / default profile slug from the typed name: lowercase +
 * hyphens. Empty (e.g. CJK-only) gets a dirty timestamped fallback so the
 * POST still has a non-empty id. Preserved from the previous inline
 * `agentIdFromName` in the sidebar.
 */
export function agentIdFromName(name: string): string {
  return slug(name).replace(/-+$/g, '') || `l2-${Date.now()}`
}

/**
 * Build the POST /api/agents body for a human-created L2 agent (裁定 18).
 * `category` is forwarded so the registry can group it under R-012.
 *
 * WP-STUDIO (裁定 36.1): `projectPath` is the folder the agent works in. It is
 * only meaningful for the workspace categories — a butler agent has no folder —
 * so we drop an empty value instead of sending `projectPath: ''` (the backend
 * would `mkdir` an empty path).
 *
 * 2026-09-16: we now also send the typed `name` **as typed** (the backend reads
 * `body["name"]` into `display`). `id` stays the lowercased slug — it doubles as
 * the Plobi profile name — but the human's casing no longer disappears at the
 * dialog. The rail still prints the id until the backend serves a display name
 * (handoff: `Docs/AGENT-TASK-AGENT-DISPLAY-NAME.md`).
 */
export function buildCreateAgentBody(
  name: string,
  category: AgentCategory,
  projectPath?: null | string
): AgentCreateRequest {
  const display = name.trim()
  const body: AgentCreateRequest = {
    id: agentIdFromName(display),
    name: display,
    role: 'l2_project',
    category
  }
  const path = projectPath?.trim()

  if (path) {
    body.projectPath = path
  }

  return body
}

/**
 * Categories whose agent needs a workspace folder before it can be created
 * (裁定 36.1). The dialog renders the folder picker for these and disables
 * submit until one is chosen; `butler` never shows the picker.
 */
export function categoryNeedsProjectPath(category: AgentCategory): boolean {
  return category === 'projects' || category === 'research'
}

/**
 * The `events` category cannot be created from this dialog yet — it needs a
 * `source_event_id` from the schedule. The dialog disables submit and shows a
 * hint instead of letting the backend return 400. Centralised so the UI and
 * tests agree on the rule.
 */
export function isCreateAgentCategoryBlocked(category: AgentCategory): boolean {
  return category === 'events'
}

/**
 * Whether submit should stay disabled for the current dialog state: blocked
 * category, missing name, or a workspace category with no folder picked yet.
 */
export function isCreateAgentSubmitDisabled(
  name: string,
  category: AgentCategory,
  projectPath?: null | string
): boolean {
  if (isCreateAgentCategoryBlocked(category)) {
    return true
  }

  if (!name.trim()) {
    return true
  }

  return categoryNeedsProjectPath(category) && !projectPath?.trim()
}
