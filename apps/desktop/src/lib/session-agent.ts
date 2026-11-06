/**
 * 会话归属键（2026-11-05 profile 连根拔 · 桌面批）。
 *
 * 归属的唯一真相是总账里的 `sessions.agent_id` —— **名册 name**，L1（总秘书）
 * 与无主行是**空串**（缺口 B 的 schema 约定，不是 undefined）。产品面不再有
 * profile：桌面这里所有「这条会话是谁的」判断都走 `agent_id`，`profile` /
 * `is_default_profile` 只剩随响应原样带过。
 *
 * 两个键要分清，混用就会复发 10-31 那次「分身自称总秘书」的错位：
 *
 * - {@link sessionAgentId}：**归属**键，名册 name 原样（trim），空串＝L1。
 *   分桶、旁路清扫、Agent 折叠都读它。
 * - {@link sessionScopeKey}：**对外传参**用的作用域键。后端
 *   `/api/agents/sessions` 的作用域解析（`_scope_name_for_read`）先按名册
 *   name 认行、再认 L1 的 `default`，理论上名册 name 原样也能过；但这条请求
 *   路径上还有 `normalizeProfileKey` 之类的老口径，所以这里仍沿用改造前
 *   `profile` 字段的取值方式：L1 → `default`，其余 trim 原样。
 */
export type SessionAgentRow = { agent_id?: null | string }

/** 归属键归一：名册 name 原样（去空白）；缺失/空串＝L1，返回 ''。 */
export function sessionAgentId(session?: null | SessionAgentRow): string {
  return (session?.agent_id ?? '').trim()
}

/** 分桶用的归一键：空串（L1）折成 `default`，其余 trim。两侧都过一遍才可比。 */
export function normalizeAgentKey(value?: null | string): string {
  return (value ?? '').trim() || 'default'
}

/** 对外传作用域时的键：L1 用 `default`（后端对它短路），其余原名。
 *  只对**确实拿到的那一行**调用——拿不到行时调用方要传 `null`（保持当前网关），
 *  不能把"没查到"当成 L1。 */
export function sessionScopeKey(session: SessionAgentRow): string {
  return normalizeAgentKey(sessionAgentId(session))
}

/**
 * 旧口径总开关（2b-1 风险①）。默认 off：走 agent_id。置 true 时整包按旧的
 * `profile` / `profile_totals` 解释，用来在不换前端的情况下退回改造前行为。
 */
export const LEGACY_SESSION_SCOPE = false

let warnedLegacyScope = false

export type ScopedSessionsResponse<T> = {
  sessions?: T[]
  total?: number
  agent_totals?: Record<string, number>
  profile_totals?: Record<string, number>
}

/**
 * 响应形状守卫（2b-1 风险①）：后端老版本（或代理改写）可能返回**没有
 * `agent_id` 字段**的行。注意 L1 的合法值就是空串 —— 只有 `undefined` / `null`
 * 才算缺，不能把空串当缺失，否则全量 L1 行都会被误判。
 *
 * 只要有一行缺字段，就**整包**退回旧口径（行上的归属键取 `profile`，L1 的
 * `default` 折回空串；总量取 `profile_totals`），并只 `console.warn` 一次。
 */
export function resolveScopedSessions<T extends { agent_id?: null | string; profile?: null | string }>(
  response: ScopedSessionsResponse<T>
): { sessions: T[]; agentTotals: Record<string, number>; total?: number; legacy: boolean } {
  const rows = response.sessions ?? []
  const isMissing = (row: T) => row?.agent_id === undefined || row?.agent_id === null
  const missing = rows.some(isMissing)
  const legacy = LEGACY_SESSION_SCOPE || missing

  if (missing && !LEGACY_SESSION_SCOPE && !warnedLegacyScope) {
    warnedLegacyScope = true
    console.warn(
      '[session-scope] 响应存在缺 agent_id 的行，整包回落旧口径（profile / profile_totals）',
      rows.filter(isMissing).slice(0, 5).map(row => (row as { id?: string })?.id ?? '<no-id>')
    )
  }

  if (!legacy) {
    return {
      sessions: rows,
      agentTotals: response.agent_totals ?? response.profile_totals ?? {},
      total: response.total,
      legacy: false
    }
  }

  return {
    sessions: rows.map(row => {
      const profileValue = (row?.profile ?? '').toString().trim()
      return { ...row, agent_id: profileValue === 'default' ? '' : profileValue }
    }),
    agentTotals: response.profile_totals ?? response.agent_totals ?? {},
    total: response.total,
    legacy: true
  }
}
