/**
 * Console API layer — the ONLY module the L1 console talks to for data.
 *
 * Backend wiring is meant to replace this file's implementations and nothing
 * else: every endpoint below mirrors the hard-frozen contract in
 * `docs/specs/ui-l1-console-spec.md` §5 and returns the same `{ ok, data }`
 * envelope shape. Callers never touch `window.plobiDesktop.api` directly.
 *
 * Current state per endpoint:
 * - `/api/agents` — MOCKED (the backend's agent/:id surface — subagents /
 *   outsourced / files / board / artifacts — is not built yet; U4, the S2 L2
 *   workbench). The L1 center does NOT use a console chat endpoint at all:
 *   per ARCH-RULINGS 2026-09-02 裁定 2 there is deliberately no
 *   `POST /api/chat` here — the center reuses the base session store + gateway
 *   path (the same `submitText` / `$messages` the full-screen chat uses).
 * - `/api/agenda/*` — LIVE, delegated to `@/plobi`, which already speaks to
 *   the real `plobi/agenda` backend. Delegating (rather than mocking) is what
 *   keeps the console and the full-screen board on one data source.
 *
 * Reuse note: no new HTTP plumbing here. Mocked endpoints answer from module
 * memory; live endpoints reuse the existing request helpers.
 */

import { confirmAgendaEvent, dismissAgendaEvent, getAgenda } from '@/plobi'
import { removeAgendaEvent, upsertAgendaEvent } from '@/store/agenda'
import type { AgendaEvent } from '@/types/plobi'

import type {
  Agent,
  AgentCreateRequest,
  AgentOverview,
  AgentSubagent,
  AgentSubagentLog,
  Artifact,
  OutsourcedApp,
  OutsourcedSessionTarget,
  ProjectFile,
  TaskBoardItem
} from './types'
import { MOCK_L2_MODEL } from './types'

/** Naive local ISO, matching the shape the agenda backend already speaks. */
function localIso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

function dayWindow(day: 'today' | 'tomorrow'): { from: string; to: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)

  if (day === 'tomorrow') {
    start.setDate(start.getDate() + 1)
  }

  const end = new Date(start)
  end.setDate(end.getDate() + 1)
  end.setMilliseconds(-1)

  return { from: localIso(start), to: localIso(end) }
}

// --- mock data (U1) --------------------------------------------------------
// One L2 worker exists at M1: the schedule secretary (spec §3.1). The 「+」
// entry is reserved for M2, so the mock deliberately ships a single row rather
// than inventing agents the backend cannot yet name.

const MOCK_AGENTS: Agent[] = [
  {
    id: 'agenda-secretary',
    model: MOCK_L2_MODEL,
    name: '日程秘书',
    status: 'working',
    todayCalls: 37
  }
]

/** Stand-in latency so the loading state is actually observable in dev. */
const MOCK_LATENCY_MS = 120

function settle<T>(value: T): Promise<T> {
  return new Promise(resolve => {
    window.setTimeout(() => resolve(value), MOCK_LATENCY_MS)
  })
}

// --- §5 endpoints ----------------------------------------------------------

function unwrapEnvelope<T>(payload: unknown): T {
  if (payload && typeof payload === 'object' && 'ok' in payload && 'data' in payload) {
    const envelope = payload as { ok: boolean; data?: T; error?: string }

    if (!envelope.ok || envelope.data === undefined) {
      throw new Error(envelope.error || 'console API request failed')
    }

    return envelope.data
  }

  return payload as T
}

/**
 * Prefer the live `GET /api/agents` (WP-BE-1). Fall back to the U1 mock when
 * the gateway is offline, the route is missing, or the registry has no L2
 * rows yet — otherwise the left rail goes empty and L2 is unreachable in
 * dogfood environments that still rely on the mock agenda-secretary.
 */
export async function fetchAgents(): Promise<Agent[]> {
  try {
    const payload = await window.plobiDesktop.api<unknown>({ path: '/api/agents' })
    const rows = unwrapEnvelope<Agent[]>(payload)

    if (Array.isArray(rows) && rows.length > 0) {
      return rows
    }
  } catch {
    // fall through to mock
  }

  return settle([...MOCK_AGENTS])
}

/** §5 POST /api/agents — human-created project L2 (裁定 18). */
export async function createAgent(req: AgentCreateRequest): Promise<Agent> {
  const payload = await window.plobiDesktop.api<unknown>({
    body: req,
    method: 'POST',
    path: '/api/agents'
  })

  return unwrapEnvelope<Agent>(payload)
}

/**
 * [SPEC-QUESTION] spec §5 names this `GET /api/agenda/events?day=today|tomorrow`
 * while the live backend (see `getAgenda` in `@/plobi`) answers
 * `GET /api/agenda?from=&to=`. Built as specified at the call site (day-based)
 * and translated here, so a contract change touches this function alone.
 */
export async function fetchAgendaEvents(day: 'today' | 'tomorrow'): Promise<AgendaEvent[]> {
  const { from, to } = dayWindow(day)

  return getAgenda(from, to)
}

/**
 * Console-only alias for "the whole agenda, no day window" — delegates to the
 * same `getAgenda()` the full-screen board uses, so the right rail and the
 * board share one source of truth (spec §3.3). The right-rail hook calls THIS
 * (not `@/plobi` directly) so the console keeps a single data-entry point and
 * never speaks to `window.plobiDesktop.api` outside this file.
 */
export async function fetchAllAgendaEvents(): Promise<AgendaEvent[]> {
  return getAgenda()
}

/**
 * Confirm or dismiss a pending event (spec §5 `POST
 * /api/agenda/events/:id/confirm` with `action: confirm|dismiss`).
 *
 * [SPEC-QUESTION] the live backend exposes confirm and dismiss as two separate
 * endpoints (`@/plobi` → `/api/agenda/:id/confirm|dismiss`), not one endpoint
 * with an `action` body. Translated here so a contract change touches this
 * function alone. Dismissing a proposed event answers `{ deleted: true }`
 * instead of a row — normalized to `ok: true` for the caller.
 *
 * Write-back: the shared `$agendaEvents` store (the SAME one the right rail and
 * full-screen board render) is updated in place, so confirming/dismissing a
 * card in the L1 center column immediately reflects in the rail without waiting
 * for the next poll (U5-seg1). Mirrors `AgendaView.handleConfirm/ Dismiss`.
 */
function isDeletedReply(value: unknown): value is { deleted: true; id: string } {
  return typeof value === 'object' && value !== null && 'deleted' in value
}

export async function confirmAgendaEventAction(
  eventId: string,
  action: 'confirm' | 'dismiss'
): Promise<{ ok: true }> {
  if (action === 'confirm') {
    const updated = await confirmAgendaEvent(eventId)

    upsertAgendaEvent(updated)

    return { ok: true }
  }

  const result = await dismissAgendaEvent(eventId)

  if (isDeletedReply(result)) {
    removeAgendaEvent(eventId)
  } else {
    upsertAgendaEvent(result)
  }

  return { ok: true }
}

// --- §5 endpoints (U4, S2 L2 workbench) ------------------------------------
// All mocked: the backend's agent/:id surface (subagents / outsourced / files
// / board / artifacts) is not built yet. Mock data is keyed by agent id; an
// unknown id rejects so the workbench can show its error state.
//
// The S2 *conversation* is NOT part of this surface: per ARCH-RULINGS 2026-09-02
// the S2 center reuses the base session store + gateway path (the same
// `submitText` / `$messages` the full-screen chat uses), so there is no
// `/api/agents/:id/chat` and no console `POST /api/chat` here (裁定 2).

const MOCK_OVERVIEW: Record<string, AgentOverview> = {
  'agenda-secretary': {
    agent: { ...MOCK_AGENTS[0] },
    sessionId: 'sess-agenda-secretary-7f3a9c',
    todayCostUsd: 0.42,
    todayTokens: 18420
  }
}

const MOCK_SUBAGENTS: Record<string, AgentSubagent[]> = {
  'agenda-secretary': [
    { id: 'sub-extractor', name: '消息提取', status: 'working' },
    { id: 'sub-confirmer', name: '确认判定', status: 'idle' },
    { id: 'sub-reminder', name: '提醒投递', status: 'error' }
  ]
}

const MOCK_OUTSOURCED: Record<string, OutsourcedApp[]> = {
  'agenda-secretary': [
    {
      iconKey: 'cursor',
      id: 'cursor',
      name: 'Cursor',
      sessions: [
        { id: 'cur-1', title: '高数课表解析' },
        { id: 'cur-2', title: '钉钉周报生成' }
      ]
    },
    {
      iconKey: 'marvis',
      id: 'marvis',
      name: 'Marvis',
      sessions: [{ id: 'mar-1', title: '行程整理' }]
    }
  ]
}

// Work logs are keyed by `<agentId>/<subagentId>`. Not in the §5 contract yet —
// see the [WAIT-BACKEND] note on `getAgentSubagentLog` below.
const MOCK_SUBAGENT_LOGS: Record<string, string[]> = {
  'agenda-secretary/sub-extractor': [
    '09:02:11 开始扫描 课程群(3) 新消息 12 条',
    '09:02:12 命中时间片段：「下周一高数改到 3-4 节」',
    '09:02:12 提交候选事件 #17（pending）',
    '09:02:13 扫描完成，无其他时间表达式'
  ],
  'agenda-secretary/sub-confirmer': [
    '09:02:20 待确认队列 1 条，等待人工确认',
    '09:02:20 空闲'
  ],
  'agenda-secretary/sub-reminder': [
    '09:05:02 投递提醒失败：DINGTALK_WEBHOOK_URL 未配置',
    '09:05:02 状态置为 error，等待重试'
  ]
}

const MOCK_FILES: Record<string, ProjectFile[]> = {
  'agenda-secretary': [
    { name: '秋季课表.md', path: 'plobi/agenda/秋季课表.md', sizeBytes: 4096, updatedAt: '2026-08-30T09:10:00' },
    { name: '待确认事件.json', path: 'plobi/agenda/待确认事件.json', sizeBytes: 2048, updatedAt: '2026-08-30T10:02:00' }
  ]
}

const MOCK_BOARD: Record<string, TaskBoardItem[]> = {
  'agenda-secretary': [
    { column: '进行中', id: 't1', title: '解析课程群消息' },
    { column: '进行中', id: 't2', title: '生成周报草稿' },
    { column: '已完成', id: 't3', title: '同步日历' }
  ]
}

const MOCK_ARTIFACTS: Record<string, Artifact[]> = {
  'agenda-secretary': [
    { id: 'a1', kind: 'doc', previewText: '本周日程摘要（Markdown）', title: '周报' },
    { id: 'a2', kind: 'image', previewText: '日程甘特图.png', title: '甘特图' }
  ]
}

function lookup<T>(table: Record<string, T[]>, id: string): T[] {
  const found = table[id]

  if (!found) {
    throw new Error(`agent "${id}" not found`)
  }

  return found.map(item => ({ ...item }))
}

function lookupOne<T>(table: Record<string, T>, id: string): T {
  const found = table[id]

  if (!found) {
    throw new Error(`agent "${id}" not found`)
  }

  return { ...found }
}

export async function getAgentOverview(agentId: string): Promise<AgentOverview> {
  try {
    const payload = await window.plobiDesktop.api<unknown>({
      path: `/api/agents/${encodeURIComponent(agentId)}/overview`
    })

    return unwrapEnvelope<AgentOverview>(payload)
  } catch {
    return settle(lookupOne(MOCK_OVERVIEW, agentId))
  }
}

export async function getAgentSubagents(agentId: string): Promise<AgentSubagent[]> {
  return settle(lookup(MOCK_SUBAGENTS, agentId))
}

export async function getAgentOutsourced(agentId: string): Promise<OutsourcedApp[]> {
  return settle(lookup(MOCK_OUTSOURCED, agentId))
}

/**
 * Work log for one L3 sub-agent (spec §6: "点击 → 跳转该子 Agent 的工作日志").
 *
 * [WAIT-BACKEND] spec §5 has no endpoint for this. Mocked locally so the
 * click-through interaction the spec hard-freezes actually exists; swap the
 * body for `window.plobiDesktop.api({ path: '/api/agents/:id/subagents/:sid/log' })`
 * once the backend grows it. An unknown sub-agent rejects so the dialog can
 * show its error state.
 */
export async function getAgentSubagentLog(agentId: string, subagentId: string): Promise<AgentSubagentLog> {
  const lines = MOCK_SUBAGENT_LOGS[`${agentId}/${subagentId}`]

  if (!lines) {
    throw new Error(`log for sub-agent "${subagentId}" not found`)
  }

  return settle({ lines: [...lines] })
}

/**
 * Deep-link target for a conversation inside an outsourced app (spec §6).
 *
 * [WAIT-BACKEND] the real 唤起机制 is soft-frozen — the backend (or a local
 * launcher registry) has to produce the per-app URL/protocol. Until then this
 * answers a plain https landing page for the app so the jump is exercisable and
 * the UI only depends on `OutsourcedSessionTarget`.
 */
export async function getOutsourcedSessionTarget(
  appId: string,
  sessionId: string
): Promise<OutsourcedSessionTarget> {
  return settle({ url: `https://example.com/plobi/outsourced/${encodeURIComponent(appId)}/${encodeURIComponent(sessionId)}` })
}

export async function getAgentFiles(agentId: string): Promise<ProjectFile[]> {
  return settle(lookup(MOCK_FILES, agentId))
}

export async function getAgentBoard(agentId: string): Promise<TaskBoardItem[]> {
  return settle(lookup(MOCK_BOARD, agentId))
}

export async function getAgentArtifacts(agentId: string): Promise<Artifact[]> {
  return settle(lookup(MOCK_ARTIFACTS, agentId))
}
