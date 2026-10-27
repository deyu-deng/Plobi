/**
 * 外部软件自己的会话（WP-EXTERNAL-SESSIONS）：取数 + 一点纯逻辑。
 *
 * 只在 Plobi 这一侧列/读/说——Qoder 桌面 App 的索引是另一套，不假装能驱动它的窗口。
 * 归属由后端按名册登记的项目目录解析，这里**不传路径**，只传 Agent 名与会话号。
 */

export type ExternalSession = {
  id: string
  project: string
  title: string
  updated_at: string
  vendor: string
}

export type ExternalSessionsData = {
  projects: string[]
  reason: string
  root?: string
  sessions: ExternalSession[]
  vendor: string
}

export type ExternalMessage = {
  role: 'assistant' | 'user'
  text: string
  ts?: string
}

export type ExternalSendResult = {
  cost_status?: string
  cost_usd?: null | number
  credits?: null | number
  duration_seconds?: number
  reply: string
}

function api<T>(path: string, init?: { body?: unknown, method?: string, timeoutMs?: number }): Promise<T> {
  return window.plobiDesktop.api<T>({ ...init, path })
}

export function getAgentExternalSessions(agentId: string): Promise<{ data: ExternalSessionsData, ok: boolean }> {
  return api(`/api/agents/${encodeURIComponent(agentId)}/external-sessions`)
}

export function getExternalTranscript(
  agentId: string,
  vendor: string,
  sessionId: string
): Promise<{ data: { id: string, messages: ExternalMessage[], project: string, vendor: string }, ok: boolean }> {
  return api(
    `/api/agents/${encodeURIComponent(agentId)}/external-sessions/${encodeURIComponent(vendor)}/${encodeURIComponent(sessionId)}`
  )
}

export function sendExternalMessage(
  agentId: string,
  vendor: string,
  sessionId: string,
  text: string
): Promise<{ data: ExternalSendResult, ok: boolean }> {
  return api(
    `/api/agents/${encodeURIComponent(agentId)}/external-sessions/${encodeURIComponent(vendor)}/${encodeURIComponent(sessionId)}/send`,
    { body: { text }, method: 'POST', timeoutMs: 660_000 }
  )
}

/** 一行上的时间：今天只写时刻，别的日子写月-日时:分。读不出就空串，不编一个。 */
export function whenText(iso: string, now = new Date()): string {
  const raw = (iso || '').trim()

  if (!raw) {
    return ''
  }

  const at = new Date(raw)

  if (Number.isNaN(at.getTime())) {
    return ''
  }

  const pad = (n: number) => String(n).padStart(2, '0')
  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`

  if (at.toDateString() === now.toDateString()) {
    return clock
  }

  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${clock}`
}

/** 标题是首条用户话，可能很长；列表里只留第一行（后端已经截到 120 字）。 */
export function titleOf(session: ExternalSession): string {
  return (session.title || '').trim().split('\n')[0] || session.id.slice(0, 8)
}
