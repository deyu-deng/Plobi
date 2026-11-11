import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/plobi'

import {
  archiveL1BypassSessions,
  bindL1GatewayIfMasterExists,
  collectL1BypassSessionIds,
  findKnownOverviewSession,
  findLatestSessionForProfile,
  forgetL1MainSession,
  l2IdentityKey,
  l2NotNavigable,
  pickL1MainSession,
  pickL1Mouth,
  profileListHasMaster,
  readL1MainSessionId,
  resolveL1HomeProfile,
  restartsMouthInPlace,
  sameCronSignature,
  secretaryShellLabel,
  secretaryShortName,
  writeL1MainSessionId
} from './desktop-controller-utils'
import { shellLevelForPath } from './routes'

const session = (id: string, title: string | null): SessionInfo => ({ id, title }) as SessionInfo

describe('sameCronSignature', () => {
  it('is false when the lengths differ', () => {
    expect(sameCronSignature([session('a', 't')], [])).toBe(false)
  })

  it('is true when ids and titles match in order', () => {
    const a = [session('a', 'one'), session('b', 'two')]
    const b = [session('a', 'one'), session('b', 'two')]
    expect(sameCronSignature(a, b)).toBe(true)
  })

  it('is false when a title changed', () => {
    const a = [session('a', 'one')]
    const b = [session('a', 'renamed')]
    expect(sameCronSignature(a, b)).toBe(false)
  })

  it('is false when order differs', () => {
    const a = [session('a', 't'), session('b', 't')]
    const b = [session('b', 't'), session('a', 't')]
    expect(sameCronSignature(a, b)).toBe(false)
  })
})

describe('bindL1GatewayIfMasterExists (WP-G3 / 裁定 6)', () => {
  it('does not call ensureGatewayProfile("master") when $profiles has no master', async () => {
    const ensureGatewayProfile = vi.fn(async () => undefined)

    await expect(
      bindL1GatewayIfMasterExists({
        ensureGatewayProfile,
        profiles: [{ name: 'default' }, { name: 'coder' }]
      })
    ).resolves.toBe('stayed')

    expect(profileListHasMaster([{ name: 'default' }, { name: 'coder' }])).toBe(false)
    expect(ensureGatewayProfile).not.toHaveBeenCalled()
  })

  it('binds master only when that profile is in the list', async () => {
    const ensureGatewayProfile = vi.fn(async () => undefined)

    await expect(
      bindL1GatewayIfMasterExists({
        ensureGatewayProfile,
        profiles: [{ name: 'default' }, { name: 'master' }]
      })
    ).resolves.toBe('bound-master')

    expect(ensureGatewayProfile).toHaveBeenCalledTimes(1)
    expect(ensureGatewayProfile).toHaveBeenCalledWith('master')
  })
})

describe('findLatestSessionForProfile (P0-1 L1 bind)', () => {
  it('prefers tip sessions on the profile, newest first', () => {
    const rows = [
      { id: 'old', agent_id: '', started_at: 1 },
      { id: 'new', agent_id: '', started_at: 9 },
      { id: 'other', agent_id: 'agenda', started_at: 99 }
    ]

    expect(findLatestSessionForProfile(rows, 'default')?.id).toBe('new')
  })

  it('skips lineage children when a tip exists', () => {
    const rows = [
      { id: 'tip', agent_id: '', started_at: 1 },
      { id: 'child', agent_id: '', started_at: 50, _lineage_root_id: 'root' }
    ]

    expect(findLatestSessionForProfile(rows, 'default')?.id).toBe('tip')
  })

  it('returns undefined when the profile has no rows', () => {
    expect(findLatestSessionForProfile([{ agent_id: 'other', id: 'a' }], 'default')).toBeUndefined()
  })
})

describe('secretaryShellLabel (WP-G8 / 裁定 13+16)', () => {
  it('labels L1 as the chief secretary', () => {
    expect(secretaryShellLabel('l1', '总秘书')).toBe('总秘书')
  })

  it('uses the known L2 short name even before agents load', () => {
    expect(secretaryShellLabel({ l2: 'agenda' }, '总秘书')).toBe('日程秘书')
    expect(secretaryShellLabel({ l2: 'agenda-secretary' }, '总秘书')).toBe('日程秘书')
  })

  it('never falls back to 总秘书 on an L2 route', () => {
    expect(secretaryShellLabel({ l2: 'custom-l2' }, '总秘书')).toBe('custom-l2')
  })

  it('prefers the short map over a long agent.name', () => {
    expect(
      secretaryShellLabel({ l2: 'agenda' }, '总秘书', [{ id: 'agenda', name: 'Kimi K3' }])
    ).toBe('日程秘书')
  })

  it('does not change when a model id is supplied as the unused name', () => {
    expect(secretaryShortName('agenda', 'workbuddy/deepseek-chat')).toBe('日程秘书')
    expect(secretaryShellLabel({ l2: 'agenda' }, '总秘书', [{ id: 'agenda', name: 'workbuddy/deepseek-chat' }])).toBe(
      '日程秘书'
    )
  })

  it('never prints a profile id: l2-agenda resolves to the short label (WP-UI-NO-INTERNALS)', () => {
    expect(secretaryShortName('l2-agenda', 'l2-agenda')).toBe('日程秘书')
  })
})

describe('pickL1MainSession (WP-G7)', () => {
  it('prefers the remembered mainline even when a newer session exists', () => {
    const rows = [
      { id: 'main', agent_id: '', started_at: 1 },
      { id: 'side', agent_id: '', started_at: 99 }
    ]

    expect(pickL1MainSession(rows, 'default', 'main')?.id).toBe('main')
  })

  it('falls back to latest when the remembered id is missing', () => {
    const rows = [
      { id: 'old', agent_id: '', started_at: 1 },
      { id: 'new', agent_id: '', started_at: 9 }
    ]

    expect(pickL1MainSession(rows, 'default', 'gone')?.id).toBe('new')
  })

  it('ignores a remembered id on another profile', () => {
    const rows = [
      { id: 'l2', agent_id: 'agenda', started_at: 50 },
      { id: 'l1', agent_id: '', started_at: 1 }
    ]

    expect(pickL1MainSession(rows, 'default', 'l2')?.id).toBe('l1')
  })
})

// 用户 10-30：「侧栏没有会话概念了。就一张嘴。永远都只有一张嘴。」
// 嘴＝这个家上**最新的一条有内容的会话**：不读"记住的主线"（它会把嘴钉在几天前那条上，
// 之后每条新对话都被归档 ⇒「聊天记录不见了」），空草稿也不参选。
describe('pickL1Mouth (one mouth, always the newest real conversation)', () => {
  it('takes the newest session that actually has messages', () => {
    const rows = [
      { id: 'old', agent_id: '', started_at: 1, message_count: 197 },
      { id: 'empty-draft', agent_id: '', started_at: 99, message_count: 0 }
    ]

    expect(pickL1Mouth(rows, 'default')?.id).toBe('old')
  })

  it('falls back to the newest row when every row is empty', () => {
    const rows = [
      { id: 'a', agent_id: '', started_at: 1, message_count: 0 },
      { id: 'b', agent_id: '', started_at: 9, message_count: 0 }
    ]

    expect(pickL1Mouth(rows, 'default')?.id).toBe('b')
  })

  it('never crosses into another profile', () => {
    const rows = [
      { id: 'l2', agent_id: 'agenda', started_at: 99, message_count: 50 },
      { id: 'l1', agent_id: '', started_at: 1, message_count: 5 }
    ]

    expect(pickL1Mouth(rows, 'default')?.id).toBe('l1')
  })

  it('keeps the desktop conversation when a newer TUI/CLI chat exists', () => {
    // 同一份家上别的线会用 `plobi chat` 做验证，那些会话会写进来；
    // 它们不许把桌面手上那张嘴抢走（用户 10-30 报「聊天记录不见了」）。
    const rows = [
      { id: 'his', agent_id: '', source: 'desktop', started_at: 1, message_count: 197 },
      { id: 'agent-test', agent_id: '', source: 'tui', started_at: 99, message_count: 6 }
    ]

    expect(pickL1Mouth(rows, 'default')?.id).toBe('his')
  })
})

// 裁定 69 「重新开始」: the L1 bind reads exactly
// `pickL1MainSession(sessions, profile, readL1MainSessionId(profile))`, so the
// pointer is the only thing that pins the mouth to a transcript. A reset that
// leaves it set hands the retired conversation straight back.
describe('forgetL1MainSession (restart this mouth)', () => {
  const rows = [
    { id: 'retired', agent_id: '', started_at: 100 },
    { id: 'fresh', agent_id: '', started_at: 200 }
  ]

  beforeEach(() => {
    localStorage.clear()
    writeL1MainSessionId('default', 'retired')
  })

  it('drops the pointer the reset relies on', () => {
    expect(readL1MainSessionId('default')).toBe('retired')

    forgetL1MainSession('default')

    expect(readL1MainSessionId('default')).toBeNull()
  })

  it('binds the post-reset transcript instead of the retired mouth', () => {
    forgetL1MainSession('default')

    expect(pickL1MainSession(rows, 'default', readL1MainSessionId('default'))?.id).toBe('fresh')
  })

  it('keeps outranking a newer session for the mainline until the user restarts', () => {
    expect(pickL1MainSession(rows, 'default', readL1MainSessionId('default'))?.id).toBe('retired')
  })
})

// 裁定 69: 「重新开始」 belongs to the mouth that is on screen, so it must not be
// expressed as a trip to `/new` — that is the mainline chat's draft route.
describe('restartsMouthInPlace (restart this mouth)', () => {
  it('resets every secretary route under the path it is already on', () => {
    for (const path of ['/', '/console', '/agent/agenda']) {
      expect(restartsMouthInPlace(shellLevelForPath(path)), path).toBe(true)
    }
  })

  it('leaves the mainline session chat to the /new draft route', () => {
    for (const path of ['/new', '/sess-123']) {
      expect(restartsMouthInPlace(shellLevelForPath(path)), path).toBe(false)
    }
  })
})

describe('resolveL1HomeProfile (L2→L1 center stale)', () => {
  const agents = ['agenda', 'l2-agenda', 'simulation']

  it('keeps a remembered default home after an L2 agent profile is active', () => {
    expect(
      resolveL1HomeProfile({
        activeProfile: 'agenda',
        agentProfiles: agents,
        rememberedHome: 'default'
      })
    ).toBe('default')
  })

  it('rejects an agent profile as remembered home and falls back to default', () => {
    expect(
      resolveL1HomeProfile({
        activeProfile: 'agenda',
        agentProfiles: agents,
        rememberedHome: 'agenda'
      })
    ).toBe('default')
  })

  it('keeps a non-agent custom profile as home', () => {
    expect(
      resolveL1HomeProfile({
        activeProfile: 'agenda',
        agentProfiles: agents,
        rememberedHome: 'work'
      })
    ).toBe('work')
  })
})

describe('collectL1BypassSessionIds', () => {
  it('keeps the mainline and drops other sessions whose agent_id is empty', () => {
    // 2026-11-05：判据是 `agent_id` 空串（L1 无主行），不再是 `profile` 标签。
    const rows = [
      { agent_id: '', id: 'main' },
      { agent_id: '', id: 'old' },
      { agent_id: 'agenda', id: 'agent' }
    ]

    expect(
      collectL1BypassSessionIds(rows, {
        agentNames: ['agenda'],
        keepId: 'main',
        profile: 'default'
      })
    ).toEqual(['old'])
  })

  it('never treats a roster-less owner as L1 (orphan rows stay put)', () => {
    // 孤儿归属（agent_id 非空但名册查不到）依旧归那个 Agent 折叠，绝不进旁路清单——
    // 否则一次 L1 入口就会把别人家的对话归档掉。
    const rows = [
      { agent_id: '', id: 'main' },
      { agent_id: 'ghost-owner', id: 'orphan' }
    ]

    expect(collectL1BypassSessionIds(rows, { keepId: 'main', profile: 'default' })).toEqual([])
  })

  it('collects nothing when there is no keep id — the 2026-10-07 shape (R-078)', () => {
    const rows = [
      { agent_id: '', id: 'a' },
      { agent_id: '', id: 'b' }
    ]

    // 这条以前断言 `['a','b']`：认不出主线时把整个 profile 交出去，配上硬删就是一次删光。
    // 63 条对话的原文就是这么没的，所以"没有主线"现在必须等于"没有旁路"。
    expect(collectL1BypassSessionIds(rows, { profile: 'default' })).toEqual([])
    expect(collectL1BypassSessionIds(rows, { keepId: '   ', profile: 'default' })).toEqual([])
  })
})

describe('archiveL1BypassSessions', () => {
  it('archives every id exactly once and never deletes', async () => {
    const calls: string[] = []

    const result = await archiveL1BypassSessions(['a', 'b'], async id => {
      calls.push(id)
    })

    expect(calls).toEqual(['a', 'b'])
    expect(result).toEqual({ archived: 2, failed: [] })
  })

  it('keeps going when one archive fails and reports it instead of throwing', async () => {
    const calls: string[] = []

    const result = await archiveL1BypassSessions(['a', 'boom', 'c'], async id => {
      calls.push(id)
      if (id === 'boom') {
        throw new Error('backend 500')
      }
    })

    expect(calls).toEqual(['a', 'boom', 'c'])
    expect(result).toEqual({ archived: 2, failed: ['boom'] })
  })

  it('does nothing for an empty list', async () => {
    const archive = vi.fn(async () => undefined)

    await expect(archiveL1BypassSessions([], archive)).resolves.toEqual({ archived: 0, failed: [] })
    expect(archive).not.toHaveBeenCalled()
  })
})

/**
 * WP-L2-MOUTH-RESUME：L2 的身份键与「不可点」判据。
 *
 * 钉的是**回归**：`5fbafaa1`（`WP-PROFILE-CANCEL`，11-06 21:07）把名册响应里那一格
 * `profile` 删掉了，桌面总控那三处读它的地方没跟着改 ⇒ 身份取到恒空串 ⇒ 每个 L2
 * 都落进「数据条目」分支、永远开一张空草稿、**从不 resume** ⇒ 对话一换界面就消失
 * （用户 11-10 23:02 报）。
 */
describe('l2IdentityKey', () => {
  it('身份键是名册那一行的 name（中文课名也认）', () => {
    expect(l2IdentityKey({ id: '大学物理（甲）Ⅱ' }, '大学物理（甲）Ⅱ')).toBe('大学物理（甲）Ⅱ')
    expect(l2IdentityKey({ id: 'Aura' }, 'Aura')).toBe('Aura')
  })

  it('名册响应里那一格 profile 不再参与身份（11-06 起后端不给）', () => {
    const row = { id: 'Aura', profile: 'aura' }

    expect(l2IdentityKey(row, 'Aura')).toBe('Aura')
  })

  it('名册缓存里还没有这一行时，回路由上那个 id（同名册行）', () => {
    expect(l2IdentityKey(undefined, 'agenda')).toBe('agenda')
  })
})

describe('l2NotNavigable', () => {
  it('后端说不可点（navigable=false）⇒ 那是数据条目，不给它起后端', () => {
    expect(l2NotNavigable({ id: 'course-row', navigable: false })).toBe(true)
  })

  it('可点 / 缺这一格（老响应）⇒ 照旧绑', () => {
    expect(l2NotNavigable({ id: 'aura', navigable: true })).toBe(false)
    expect(l2NotNavigable({ id: 'aura' })).toBe(false)
    expect(l2NotNavigable(undefined)).toBe(false)
  })
})
