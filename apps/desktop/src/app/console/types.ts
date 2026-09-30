/**
 * L1 console domain types.
 *
 * Shapes follow the hard-frozen public API contract in
 * `Docs/specs/console-ui.md` §5 (response envelope `{ ok, data }`) and
 * the agent state machine in §4.2.
 *
 * Reuse note: agenda events are NOT re-declared here — the console shares
 * `AgendaEvent` from `@/types/plobi` with the existing full-screen board so
 * there is exactly one shape for schedule rows.
 */

import type { AgendaEvent } from '@/types/plobi'

/** Every console response is unwrapped from this envelope. */
export interface ApiEnvelope<T> {
  data?: T
  error?: string
  ok: boolean
}

/** §4.2 — the four agent states the left rail must be able to render. */
export type AgentStatus = 'awaiting_approval' | 'error' | 'idle' | 'working'

export const AGENT_STATUSES: readonly AgentStatus[] = [
  'idle',
  'working',
  'awaiting_approval',
  'error'
]

/**
 * §5 POST /api/agents — human-created L2 (配备).
 * L1 still must not auto-spawn except one agenda L2 (S2).
 */
export interface AgentCreateRequest {
  /** Registry id / default profile name, e.g. `plobi-code`. */
  id: string
  /**
   * Sidebar taxonomy group (R-012): `projects` / `butler` / `events` /
   * `research`. Forwarded to the agent registry; read-side defaults to
   * `butler` when absent (see `Agent.category`). Added by WP-CREATE-CAT.
   */
  category?: string
  /**
   * Workspace the agent works in (WP-STUDIO, 裁定 36.1). The backend already
   * accepts this and `mkdir`s it (`POST /api/agents`); the dialog only sends
   * it for the categories that need a folder (`projects` / `research`) and
   * never sends it for `butler`.
   */
  projectPath?: string
  /** Default `l2_project`. Never `l1_secretary`. */
  role?: 'l2_agenda' | 'l2_planner' | 'l2_project'
  /**
   * Human-typed display name, now sent **as typed** (2026-09-16). The backend
   * reads `body["name"]` into its `display` slot; `id` stays the lowercased
   * slug because it doubles as the Plobi profile name. The rail still prints
   * `id` until the backend serves a display name on the agent row — see
   * `Docs/AGENT-TASK-AGENT-DISPLAY-NAME.md`.
   */
  name?: string
  description?: string
  mindSubtree?: string
  cloneFrom?: string
}

/** L1-safe plan snapshot on `plobi_secretary_ask` (no plan_items). */
export type DailyPlanStatus = 'empty' | 'pending' | 'confirmed' | 'dismissed' | 'missing'

export interface DailyPlanL1View {
  conflict_count: number
  empty: boolean
  event_count: number
  for_date: string
  stale: boolean
  status: DailyPlanStatus
  summary: string
}

/** §5 GET /api/agents — one row per L2 worker. */
export interface Agent {
  /**
   * Sidebar taxonomy group (R-012): `projects` / `butler` / `events` /
   * `research`. Served by the backend agent registry; legacy rows without the
   * field fold into `butler` on the client.
   */
  category?: string
  id: string
  /** Qualified model id, e.g. `deepseek/deepseek-chat` (ADR-0011 routing). Absent when the role has no route. */
  model?: string
  name: string
  /**
   * Plobi profile this agent chats under (`registry.profile_name`). The
   * desktop switches the live gateway onto this profile when entering L2 so
   * L1/L2 transcripts stay isolated (ARCH-RULINGS 裁定 1/3).
   */
  profile?: string
  status: AgentStatus
  todayCalls: number
}

/** §5 GET /api/agents/:id/overview — S2 status card (U4). */
export interface AgentOverview {
  agent: Agent
  /** Independent per-agent session identity (§6). */
  sessionId: string
  todayCostUsd: number
  todayTokens: number
  /**
   * R-013 (裁定 20): the folder this L2 agent is bound to (backend
   * `project_path`). Absent/empty for butler-type agents with no project —
   * the file tree must stay empty then, never inherit the previous cwd.
   */
  projectPath?: string
}

export type AgentTaskResult = 'error' | 'failed' | 'ok' | 'pending'

/** §5 GET /api/agents/:id/tasks — S2 task stream (U4). */
export interface AgentTask {
  at: string
  /** Related event number, when the task acted on a schedule item. */
  eventSeq?: number
  id: string
  label: string
  outcome: string
  result: AgentTaskResult
}

/** §5 GET /api/agents/:id/subagents — an L3 child agent (spec §6 left rail). */
export interface AgentSubagent {
  id: string
  name: string
  status: AgentStatus
}

/**
 * §5 GET /api/agents/:id/outsourced — an external app the L2 delegates work
 * to (spec §6 left rail). Clicking a session jumps to that app's conversation.
 */
export interface OutsourcedApp {
  /** Stable id, e.g. `cursor`, `marvis`, `antigravity`. */
  id: string
  /** Display name, e.g. "Cursor". */
  name: string
  /** Icon key the UI resolves to a Codicon / asset (§6: icons required). */
  iconKey: string
  sessions: OutsourcedSession[]
}

export interface OutsourcedSession {
  /** Conversation id inside the external app. */
  id: string
  title: string
}

/**
 * §6 work log: clicking an L3 sub-agent opens its log. The endpoint is not in
 * §5 yet — see the [WAIT-BACKEND] note on `getAgentSubagentLog`.
 */
export interface AgentSubagentLog {
  /** Raw log lines, oldest first (rendered verbatim by `LogView`). */
  lines: string[]
}

/**
 * Where an outsourced conversation lives inside its external app.
 *
 * [SPEC-QUESTION] §6 hard-freezes "click a conversation → jump to that app's
 * conversation" but soft-freezes the *唤起机制* (protocol / CLI / deep link).
 * Modelled as a plain URL so the UI can reuse the base `openExternalLink`
 * helper and only this shape has to change when the mechanism is decided.
 */
export interface OutsourcedSessionTarget {
  url: string
}

/** §5 GET /api/agents/:id/files — S2 right-rail project file board (spec §6). */
export interface ProjectFile {
  name: string
  path: string
  sizeBytes: number
  updatedAt: string
}

/** §5 GET /api/agents/:id/board — S2 right-rail task kanban (spec §6). */
export interface TaskBoardItem {
  column: string
  id: string
  title: string
}

/** §5 GET /api/agents/:id/artifacts — S2 right-rail artifact preview (§6). */
export interface Artifact {
  id: string
  kind: 'code' | 'doc' | 'image'
  title: string
  /** Short preview text (path, snippet, or caption) for the card body. */
  previewText: string
}

/**
 * L2 model name for mock data. Single source of truth so the model name
 * is not duplicated across mock data + test fixtures. ADR-0011 specifies
 * L2 uses "便宜模型或 aigw 聚合额度"; the actual default is in backend
 * config, this constant only governs mock/test data.
 */
export const MOCK_L2_MODEL = 'deepseek/deepseek-chat'

/** Re-exported so console modules never import the board's types ad hoc. */
export type ConsoleAgendaEvent = AgendaEvent
