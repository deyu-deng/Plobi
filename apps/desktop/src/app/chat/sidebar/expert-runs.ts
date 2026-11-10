/** L2 派出去的临时专家（WP-L3-EXPERTS）：名单来自子会话，日志复用 sessions 路由。 */

export type ExpertRun = {
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

export type ExpertRunsData = {
  experts: ExpertRun[]
  live?: number
  reason?: string
}

export type ExpertLogMessage = {
  content?: string
  role?: string
  tool_calls?: { function?: { name?: string } }[]
  tool_name?: string
}

function api<T>(path: string, init?: { body?: unknown, method?: string }): Promise<T> {
  return window.plobiDesktop.api<T>({ ...init, path })
}

export function getAgentExperts(agentId: string): Promise<{ data: ExpertRunsData, ok: boolean }> {
  return api(`/api/agents/${encodeURIComponent(agentId)}/experts`)
}

/** 一类角色的配置：职责、工具面、以及**它自己用的模型**（null＝跟随父 Agent）。 */
export type ExpertRoleInfo = {
  duty: string
  key: string
  label: string
  mark: string
  model: null | string
  toolsets: string[]
}

export function getExpertRoles(): Promise<{ data: { roles: ExpertRoleInfo[] }, ok: boolean }> {
  return api('/api/experts/roles')
}

/** 写一类角色的模型。`model` 传空串＝清掉覆盖，回到继承父 Agent。 */
export function setExpertRoleModel(
  role: string,
  model: string
): Promise<{ data: { model: null | string, role: string }, ok: boolean }> {
  return api(`/api/experts/roles/${encodeURIComponent(role)}`, {
    body: { model },
    method: 'PUT'
  })
}

export function getExpertLog(sessionId: string): Promise<{ messages: ExpertLogMessage[], session_id: string }> {
  return api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`)
}

/** 一行里放得下的目标：第一行、最多 80 字。 */
export function goalLine(expert: ExpertRun): string {
  const text = (expert.goal || '').trim().split('\n')[0]

  return text.length > 80 ? `${text.slice(0, 80)}…` : text || expert.id
}

/** 这一趟干了多久：正在干的算到此刻，干完的用消息/工具读数兜底。 */
export function elapsedText(expert: ExpertRun, now = Date.now()): string {
  // 只给**正在干**的算到此刻；干完的不显示时长（旧记录会写成「31623m」那种荒谬读数）。
  if (!expert.started_at || expert.status !== 'running') {
    return ''
  }

  const seconds = Math.max(0, Math.round(now / 1000 - expert.started_at))

  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** 日志里一行说什么：用户/专家的话、或者它调了哪枚工具。 */
export function logLine(message: ExpertLogMessage): { kind: 'say' | 'tool', text: string } {
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
