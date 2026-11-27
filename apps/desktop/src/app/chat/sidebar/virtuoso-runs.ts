/** L2 派出去的临时专家（WP-L3-EXPERTS）：名单来自子会话，日志复用 sessions 路由。 */

export type VirtuosoRun = {
  goal: string
  id: string
  messages: number
  model: string
  parent_session_id: string
  role?: string
  started_at: number
  status: 'done' | 'running'
  ticket_id?: string
  ticket_status?: string
  tool_calls: number
}

export type VirtuosoRunsData = {
  virtuosos: VirtuosoRun[]
  live?: number
  reason?: string
}

export type VirtuosoLogMessage = {
  content?: string
  role?: string
  tool_calls?: { function?: { name?: string } }[]
  tool_name?: string
}

function api<T>(path: string, init?: { body?: unknown, method?: string }): Promise<T> {
  return window.plobiDesktop.api<T>({ ...init, path })
}

export function getAgentVirtuosos(agentId: string): Promise<{ data: VirtuosoRunsData, ok: boolean }> {
  return api(`/api/agents/${encodeURIComponent(agentId)}/virtuosos`)
}

/**
 * 能力池里的一项：**键** ＋ 人话标签。
 *
 * 键用来回写（黑名单只认键），标签用来上屏。后端给的标签是中文，屏上原样显示——
 * 前端不自己译那一套（`Docs/reference/glossary.md` 的口径：机器词不上屏，人话标签照抄）。
 */
export type VirtuosoCapability = {
  key: string
  label: string
}

/**
 * 一类角色的配置：职责、工具面、**它自己用的模型**（null＝跟随父 Agent），
 * 以及它的能力三件套。
 *
 * ⚠ **两套名字，别混**（`capabilities` / `effective_capabilities` /
 * `missing_capabilities` 是**人话标签**；`disabled_capabilities` 与能力池里的
 * `key` 是**能力项键**）：
 * - `capabilities` —— 这一类**默认**有的能力（人话标签）；
 * - `effective_capabilities` —— 它**现在**真拿到的（人话标签）；
 * - `missing_capabilities` —— 默认有、现在没有的（人话标签）；
 * - `disabled_capabilities` —— 它**自己**的黑名单（**能力项键**）。
 */
export type VirtuosoRoleInfo = {
  /** 这一类**默认**有的能力（人话标签）。 */
  capabilities: string[]
  /** 这一类**现在**真拿到的能力（人话标签）。 */
  effective_capabilities: string[]
  /** 这一类**现在**真拿到的工具门类。 */
  effective_toolsets: string[]
  /** 它**自己**关掉的能力（能力项**键**，如 `run_commands`）。 */
  disabled_capabilities: string[]
  duty: string
  key: string
  label: string
  mark: string
  /** 默认有、现在没有的（人话标签）。 */
  missing_capabilities: string[]
  model: null | string
  toolsets: string[]
}

/** `GET /api/virtuosos/roles` 的整个身子。 */
export type VirtuosoRolesResponse = {
  data: {
    /** 能力池（键 ＋ 人话标签）——画勾选框要靠它把标签认回键。 */
    capabilities?: VirtuosoCapability[]
    roles: VirtuosoRoleInfo[]
  }
  ok: boolean
}

export function getVirtuosoRoles(): Promise<VirtuosoRolesResponse> {
  return api('/api/virtuosos/roles')
}

/**
 * 写一类角色**自己**的黑名单（能力项键）。空数组＝回到默认全开。
 *
 * 只动这一格：后端收到 `disabled_capabilities` 就只写黑名单，**不碰模型**。
 */
export function setVirtuosoRoleDisabledCapabilities(
  role: string,
  disabled: string[]
): Promise<{ data: { disabled_capabilities: string[], model: null | string, role: string }, ok: boolean }> {
  return api(`/api/virtuosos/roles/${encodeURIComponent(role)}`, {
    body: { disabled_capabilities: disabled },
    method: 'PUT'
  })
}

/** 写一类角色的模型。`model` 传空串＝清掉覆盖，回到继承父 Agent。 */
export function setVirtuosoRoleModel(
  role: string,
  model: string
): Promise<{ data: { model: null | string, role: string }, ok: boolean }> {
  return api(`/api/virtuosos/roles/${encodeURIComponent(role)}`, {
    body: { model },
    method: 'PUT'
  })
}

export function getVirtuosoLog(sessionId: string): Promise<{ messages: VirtuosoLogMessage[], session_id: string }> {
  return api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`)
}

/** 一行里放得下的目标：第一行、最多 80 字。 */
export function goalLine(virtuoso: VirtuosoRun): string {
  const text = (virtuoso.goal || '').trim().split('\n')[0]

  return text.length > 80 ? `${text.slice(0, 80)}…` : text || virtuoso.id
}

/** 这一趟干了多久：正在干的算到此刻，干完的用消息/工具读数兜底。 */
export function elapsedText(virtuoso: VirtuosoRun, now = Date.now()): string {
  // 只给**正在干**的算到此刻；干完的不显示时长（旧记录会写成「31623m」那种荒谬读数）。
  if (!virtuoso.started_at || virtuoso.status !== 'running') {
    return ''
  }

  const seconds = Math.max(0, Math.round(now / 1000 - virtuoso.started_at))

  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** 日志里一行说什么：用户/专家的话、或者它调了哪枚工具。 */
export function logLine(message: VirtuosoLogMessage): { kind: 'say' | 'tool', text: string } {
  const role = (message.role || '').trim()

  if (role === 'tool') {
    const name = (message.tool_name || '').trim()

    return { kind: 'tool', text: name ? `⚙ ${name}` : '⚙' }
  }

  const text = String(message.content || '').trim()

  if (text) {
    return { kind: 'say', text }
  }

  const calls = (message.tool_calls || [])
    .map(call => call?.function?.name || '')
    .filter(Boolean)

  return { kind: 'tool', text: calls.length ? `⚙ ${calls.join(' · ')}` : '' }
}
