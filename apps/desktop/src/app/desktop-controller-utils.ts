import type { SessionInfo } from '@/plobi'
import { normalizeAgentKey, sessionAgentId } from '@/lib/session-agent'
import { persistString, storedString } from '@/lib/storage'
import { normalizeProfileKey } from '@/store/scope'

import type { ShellLevel } from './routes'

/**
 * L2 那一行在名册里的样子（`console/types.ts::Agent` 的一个子集）。
 *
 * 身份键取名册 name（名册行即身份，裁定 178 乙案＋裁定 180 单库 `sessions.agent_id`
 * ＋`WP-AGENT-SINGLE-GATEWAY` §3.4「身份是请求属性」）。
 */
export interface L2RosterRow {
  id?: string
  name?: string
  navigable?: boolean
}

/**
 * L2 的身份键：**名册那一行的 name**。
 *
 * 2026-11-05 起（裁 178 乙案「名册行即身份」＋裁 180「单库 ＋ `sessions.agent_id`
 * ＝名册 name」＋SINGLE-GATEWAY §3.4「身份是请求属性，不是进程属性」）：桌面把它随
 * 请求带下去（REST 走 `X-Plobi-Agent` 头、非 ASCII 退到 `?profile=`；WS 走
 * `session.create` 的 `profile` 参数），后端按名册行解析，落到 `sessions.agent_id`。
 *
 * ⚠️ **不许再从名册响应里那一格 `profile` 取身份**：它 11-06 已经被
 * `5fbafaa1`（`WP-PROFILE-CANCEL`）从响应里删掉了 ⇒ 取到的是恒空串 ⇒ 每个 L2 都
 * 落进「数据条目」分支、永远开一张空草稿、**从不 resume** ⇒ 对话一换界面就消失
 * （用户 11-10 23:02 报）。那个派生 slug（`derive_runtime_slug` ＋
 * `runtime_slugs.yaml`）在后端，桌面算不出来，也不需要——名册行自己的 `id` 就是它。
 */
export function l2IdentityKey(row: L2RosterRow | undefined, agentId: string): string {
  return (row?.id ?? '').trim() || (agentId ?? '').trim()
}

/**
 * 这一行点进去会不会必然失败：判据只有后端那一格 `navigable`（R-072）。
 *
 * 「数据条目」（没有运行时可绑的那种行）不该给它起后端、也不许替它编一个家 ⇒ 走
 * 与「绑不上」同一条既有路径：留在原地、开一张新草稿，不排任何重试。
 */
export function l2NotNavigable(row: L2RosterRow | undefined): boolean {
  return Boolean(row && row.navigable === false)
}

// Cheap signature compare so a poll only swaps the atom (and re-renders the
// sidebar) when the visible rows actually changed.
export function sameCronSignature(a: SessionInfo[], b: SessionInfo[]): boolean {
  if (a.length !== b.length) {
    return false
  }

  return a.every((session, i) => {
    const other = b[i]

    return (
      other != null &&
      session.id === other.id &&
      session._lineage_root_id === other._lineage_root_id &&
      session.title === other.title &&
      session.source === other.source &&
      session.agent_id === other.agent_id &&
      session.preview === other.preview &&
      session.message_count === other.message_count &&
      session.last_active === other.last_active &&
      session.ended_at === other.ended_at
    )
  })
}

/** True only when a Plobi profile named `master` is actually installed. */
export function profileListHasMaster(profiles: Array<{ name: string }>): boolean {
  return profiles.some(profile => normalizeProfileKey(profile.name) === 'master')
}

/**
 * L1 may point the live gateway at `master` only when that profile exists
 * (ARCH-RULINGS 2026-09-04 裁定 6). Missing `master` must leave the current
 * profile and socket alone — never `ensureGatewayProfile('master')`.
 */
export async function bindL1GatewayIfMasterExists(options: {
  ensureGatewayProfile: (profile: string) => Promise<void>
  profiles: Array<{ name: string }>
}): Promise<'bound-master' | 'stayed'> {
  if (!profileListHasMaster(options.profiles)) {
    return 'stayed'
  }

  await options.ensureGatewayProfile('master')

  return 'bound-master'
}

/** Match an L2 overview session id against the desktop's known session list (N3). */
export function findKnownOverviewSession<T extends { id: string; _lineage_root_id?: null | string }>(
  sessions: T[],
  sessionId: string
): T | undefined {
  const id = sessionId.trim()

  if (!id) {
    return undefined
  }

  return sessions.find(session => session.id === id || session._lineage_root_id === id)
}

/**
 * Latest non-lineage session owned by one agent (L1 bind after 裁定 6).
 * Prefers tip sessions (`!_lineage_root_id`); falls back to any row on that owner.
 *
 * 2026-11-05 起归属键是行级 `agent_id`（名册 name，L1＝空串），比较前两侧都过
 * {@link normalizeAgentKey}（空串折成 `default`）——传进来的仍是调用方那份
 * 作用域键（`default` / 名册 name），只是不再读 `profile` 标签。
 */
export function findLatestSessionForProfile<
  T extends { id: string; agent_id?: null | string; _lineage_root_id?: null | string; started_at?: null | number }
>(sessions: T[], profile: string): T | undefined {
  const key = normalizeAgentKey(profile)
  const onProfile = sessions.filter(session => normalizeAgentKey(session.agent_id) === key)

  if (onProfile.length === 0) {
    return undefined
  }

  const tips = onProfile.filter(session => !session._lineage_root_id)
  const pool = tips.length > 0 ? tips : onProfile

  return [...pool].sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0]
}

/**
 * 总秘书**那一张嘴**：这个家上最新的一条**有内容**的会话。
 *
 * 用户 10-30：「侧栏没有会话概念了。就一张嘴。永远都只有一张嘴。」
 * ⇒ 嘴就是「最近那条真对话」：
 * ① **不读"记住的主线"**（那份记忆会把嘴钉在几天前那条上——现读钉在 10-07，
 *    于是之后每一条新对话都被当成旁路收进归档 ⇒ 用户看到的就是「聊天记录不见了」）；
 * ② **0 条消息的空草稿不参选**（否则界面一开一个新建草稿，嘴就被抢走）；
 * ③ **优先桌面自己在用的那条**（`source === 'desktop'`）：同一份家上还有 TUI / CLI
 *    写进来的会话（别的线用 `plobi chat` 做验证会留下），那些不该把手上的嘴抢走；
 *    桌面一条都没有时才退到"任意来源里最新那条"。
 */
export function pickL1Mouth<
  T extends {
    id: string
    agent_id?: null | string
    source?: null | string
    _lineage_root_id?: null | string
    started_at?: null | number
    message_count?: null | number
  }
>(sessions: T[], profile: string): T | undefined {
  const withHistory = sessions.filter(session => (session.message_count ?? 0) > 0)
  const desktopChats = withHistory.filter(session => (session.source ?? '') === 'desktop')

  const pool = desktopChats.length > 0 ? desktopChats : withHistory.length > 0 ? withHistory : sessions

  return findLatestSessionForProfile(pool, profile)
}

export function l1MainSessionKey(profile: string): string {
  return `plobi.desktop.l1MainSession.${normalizeProfileKey(profile)}`
}

export function readL1MainSessionId(profile: string): null | string {
  return storedString(l1MainSessionKey(profile))
}

export function writeL1MainSessionId(profile: string, id: null | string): void {
  persistString(l1MainSessionKey(profile), id)
}

/**
 * 「重新开始」 retires the transcript this profile's mouth points at. Every L1
 * bind prefers the remembered mainline over "latest" (`pickL1MainSession`), so
 * leaving it set hands the retired conversation back on the next re-bind. The
 * next message re-seeds it from the session it creates.
 */
export function forgetL1MainSession(profile: string): void {
  writeL1MainSessionId(profile, null)
}

/**
 * 裁定 69: 「重新开始」 restarts the Agent's one mouth under the route it is
 * already on. Only the mainline session chat (`shellLevelForPath` → null) keeps
 * the `/new` draft route; a secretary shell detouring through `/new` nulls its
 * level, bounces the center home, flashes the rails, and lets the level-keyed
 * bind resume the transcript the user just retired.
 *
 * Pure predicate so the rule is testable without rendering the shell.
 */
export function restartsMouthInPlace(level: null | ShellLevel): boolean {
  return level !== null
}

/** Known L2 short labels (裁定 13). Never use description / model as identity. */
const L2_SHORT_NAMES: Record<string, string> = {
  agenda: '日程秘书',
  'agenda-secretary': '日程秘书',
  'secretary-agenda': '日程秘书'
}

export function secretaryShortName(agentId: string, fallbackName?: string): string {
  const key = agentId.trim().toLowerCase()
  // WP-UI-NO-INTERNALS: the rail must never print a profile id (`l2-agenda`).
  // Try the id as given, then the profile-stripped form (`l2-agenda` → `agenda`).
  const mapped = key ? (L2_SHORT_NAMES[key] ?? L2_SHORT_NAMES[key.replace(/^l[12][-_]/, '')]) : undefined

  if (mapped) {
    return mapped
  }

  const named = (fallbackName || '').trim()

  if (named) {
    return named
  }

  return agentId.trim()
}

/**
 * Shell identity copy (裁定 16): L1 = 总秘书, L2 = short name.
 * Derived only from ShellLevel (+ optional agent list). Model / profile / session
 * title must not change this string.
 */
export function secretaryShellLabel(
  level: 'l1' | { l2: string } | null | undefined,
  chiefSecretary: string,
  agents?: ReadonlyArray<{ id: string; name?: string }>
): string {
  if (level && typeof level === 'object') {
    const agent = agents?.find(row => row.id === level.l2)

    return secretaryShortName(level.l2, agent?.name) || chiefSecretary
  }

  return chiefSecretary
}

/** Prefer the remembered L1 mainline; otherwise the latest tip on that profile. */
export function pickL1MainSession<
  T extends { id: string; agent_id?: null | string; _lineage_root_id?: null | string; started_at?: null | number }
>(sessions: T[], profile: string, rememberedId?: null | string): T | undefined {
  const key = normalizeAgentKey(profile)
  const remembered = (rememberedId || '').trim()

  if (remembered) {
    const hit = sessions.find(
      session => session.id === remembered && normalizeAgentKey(session.agent_id) === key
    )

    if (hit) {
      return hit
    }
  }

  return findLatestSessionForProfile(sessions, profile)
}

/**
 * Every L1-home local session except the kept mainline. Cron / messaging /
 * subagent / agent-owned threads are left alone — those are not "bypass chats".
 * Without a mainline to keep, nothing counts as a bypass: see the guard below.
 *
 * 2026-11-05：L1 判据从「`profile` 标签等于 L1 那份家」改成 **`agent_id` 为空串**
 * （schema 约定，缺口 B）。这一条同时替掉了旧的 `agentProfiles` 白名单兜底——
 * 归属非空就一定是某个 Agent 的会话，不必再拿家名集合去比。
 */
export function collectL1BypassSessionIds(
  sessions: ReadonlyArray<{ id: string; agent_id?: null | string }>,
  options: {
    /** 名册里存在的 Agent 名。归属非空的行本就跳过，留着只为调用方语义自证。 */
    agentNames?: Iterable<string>
    keepId?: null | string
    /** 这次要扫的是哪一份 L1（`default`）。判据已不看它，仅供调用方自证。 */
    profile?: string
  }
): string[] {
  const keep = (options.keepId || '').trim()

  // Returning every sibling when `keep` is empty is what ate a 63-message
  // transcript on 2026-10-07 (R-078): the sweep ran while L1 was still a fresh
  // draft, so there was no mainline and the whole profile went with it.
  if (!keep) {
    return []
  }

  const ids: string[] = []

  for (const session of sessions) {
    if (session.id === keep) {
      continue
    }

    // L1 判据：归属键为空串。非空＝那个 Agent 的会话，归它折叠，永不当旁路清掉。
    if (sessionAgentId(session)) {
      continue
    }

    ids.push(session.id)
  }

  return ids
}

/**
 * Archive the L1 bypass sessions — never delete them. Session lists default to
 * `archived=exclude`, so the rail still shows one mouth while the transcripts stay
 * in the DB (裁定 6 / R-078: 会话是 L1 的短时记忆，不许清空).
 *
 * Failures are collected, not thrown: a session that did not archive stays visible,
 * which is the safe direction, and the next L1 entry retries it.
 */
export async function archiveL1BypassSessions(
  ids: ReadonlyArray<string>,
  archive: (id: string) => Promise<unknown>
): Promise<{ archived: number; failed: string[] }> {
  const failed: string[] = []
  let archived = 0

  for (const id of ids) {
    try {
      await archive(id)
      archived += 1
    } catch {
      failed.push(id)
    }
  }

  return { archived, failed }
}

/**
 * Profile to bind when L1 returns without a `master` profile (裁定 6).
 *
 * After an L2 visit the live gateway is often still on the agent profile. If we
 * "stay" there, `pickL1MainSession` resumes the L2 transcript under L1 chrome
 * (返回总秘书 → center still looks like the agent). Prefer a remembered L1
 * home; never treat a known agent profile as L1 home.
 */
export function resolveL1HomeProfile(options: {
  activeProfile?: null | string
  agentProfiles?: Iterable<string>
  rememberedHome?: null | string
}): string {
  const agentSet = new Set(
    [...(options.agentProfiles ?? [])].map(name => normalizeProfileKey(name)).filter(Boolean)
  )

  const pick = (raw: null | string | undefined): string | null => {
    const key = normalizeProfileKey((raw || '').trim() || '')

    if (!key) {
      return null
    }

    if (key === 'master' || key === 'default') {
      return key
    }

    if (agentSet.has(key)) {
      return null
    }

    return key
  }

  return pick(options.rememberedHome) || pick(options.activeProfile) || 'default'
}
