/**
 * 外部软件自己的会话（WP-EXTERNAL-SESSIONS）：取数 + 一点纯逻辑。
 *
 * 只在 Plobi 这一侧列/读/说——Qoder 桌面 App 的索引是另一套，不假装能驱动它的窗口。
 * 归属由后端按名册登记的项目目录解析，这里**不传路径**，只传 Agent 名与会话号。
 */

export type ExternalSession = {
  archived?: boolean
  id: string
  pinned?: boolean
  project: string
  /** `true` 相关 / `null` 待判断 / `false` L2 标过无关（面板不显示）。 */
  related: boolean | null
  related_source: 'l2' | 'path' | 'title' | 'unknown' | string
  sendable: boolean
  title: string
  updated_at: string
  vendor: string
}

export type ExternalSessionSource = {
  readable: boolean
  reason: string
  root: string
  sendable: boolean
  sessions: number
  vendor: string
}

export type ExternalSessionsData = {
  projects: string[]
  reason: string
  sessions: ExternalSession[]
  sources?: ExternalSessionSource[]
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

export function getSessionSources(): Promise<{ data: { sources: ExternalSessionSource[] }, ok: boolean }> {
  return api('/api/external-sessions/sources')
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

export type VendorGroup = { sessions: ExternalSession[], vendor: string }

/** 按软件分组；组内新的在前，组间按固定顺序（不跟着条数跳）。 */
const VENDOR_ORDER = ['qoder', 'codebuddy', 'marvis', 'cursor', 'agy']

export function groupByVendor(sessions: readonly ExternalSession[]): VendorGroup[] {
  const byVendor = new Map<string, ExternalSession[]>()

  for (const session of sessions) {
    const list = byVendor.get(session.vendor)

    if (list) {
      list.push(session)
    } else {
      byVendor.set(session.vendor, [session])
    }
  }

  return [...byVendor.entries()]
    .map(([vendor, list]) => ({
      sessions: [...list].sort((a, b) => String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? ''))),
      vendor,
    }))
    .sort((a, b) => {
      const ai = VENDOR_ORDER.indexOf(a.vendor)
      const bi = VENDOR_ORDER.indexOf(b.vendor)

      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi)
    })
}

export function vendorLabel(vendor: string): string {
  const known: Record<string, string> = {
    agy: 'agy',
    codebuddy: 'CodeBuddy',
    cursor: 'Cursor',
    marvis: 'Marvis',
    qoder: 'Qoder'
  }

  return known[vendor] ?? vendor
}

export function patchExternalSession(
  agentId: string,
  vendor: string,
  sessionId: string,
  fields: { archived?: boolean, pinned?: boolean, title?: string }
): Promise<{ data: { entry?: Record<string, unknown> }, ok: boolean }> {
  return api(
    `/api/agents/${encodeURIComponent(agentId)}/external-sessions/${encodeURIComponent(vendor)}/${encodeURIComponent(sessionId)}`,
    { body: fields, method: 'PATCH' }
  )
}

export function deleteExternalSession(
  agentId: string,
  vendor: string,
  sessionId: string
): Promise<{ data: Record<string, unknown>, ok: boolean }> {
  return api(
    `/api/agents/${encodeURIComponent(agentId)}/external-sessions/${encodeURIComponent(vendor)}/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' }
  )
}

export function restoreExternalSession(
  agentId: string,
  vendor: string,
  sessionId: string
): Promise<{ data: Record<string, unknown>, ok: boolean }> {
  return api(
    `/api/agents/${encodeURIComponent(agentId)}/external-sessions/${encodeURIComponent(vendor)}/${encodeURIComponent(sessionId)}/restore`,
    { method: 'POST' }
  )
}
