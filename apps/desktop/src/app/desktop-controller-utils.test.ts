import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/plobi'

import {
  bindL1GatewayIfMasterExists,
  collectL1BypassSessionIds,
  findKnownOverviewSession,
  findLatestSessionForProfile,
  forgetL1MainSession,
  pickL1MainSession,
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
      { id: 'old', profile: 'default', started_at: 1 },
      { id: 'new', profile: 'default', started_at: 9 },
      { id: 'other', profile: 'l2-agenda', started_at: 99 }
    ]

    expect(findLatestSessionForProfile(rows, 'default')?.id).toBe('new')
  })

  it('skips lineage children when a tip exists', () => {
    const rows = [
      { id: 'tip', profile: 'default', started_at: 1 },
      { id: 'child', profile: 'default', started_at: 50, _lineage_root_id: 'root' }
    ]

    expect(findLatestSessionForProfile(rows, 'default')?.id).toBe('tip')
  })

  it('returns undefined when the profile has no rows', () => {
    expect(findLatestSessionForProfile([{ id: 'a', profile: 'other' }], 'default')).toBeUndefined()
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
      { id: 'main', profile: 'default', started_at: 1 },
      { id: 'side', profile: 'default', started_at: 99 }
    ]

    expect(pickL1MainSession(rows, 'default', 'main')?.id).toBe('main')
  })

  it('falls back to latest when the remembered id is missing', () => {
    const rows = [
      { id: 'old', profile: 'default', started_at: 1 },
      { id: 'new', profile: 'default', started_at: 9 }
    ]

    expect(pickL1MainSession(rows, 'default', 'gone')?.id).toBe('new')
  })

  it('ignores a remembered id on another profile', () => {
    const rows = [
      { id: 'l2', profile: 'l2-agenda', started_at: 50 },
      { id: 'l1', profile: 'default', started_at: 1 }
    ]

    expect(pickL1MainSession(rows, 'default', 'l2')?.id).toBe('l1')
  })
})

// 裁定 69 「重新开始」: the L1 bind reads exactly
// `pickL1MainSession(sessions, profile, readL1MainSessionId(profile))`, so the
// pointer is the only thing that pins the mouth to a transcript. A reset that
// leaves it set hands the retired conversation straight back.
describe('forgetL1MainSession (restart this mouth)', () => {
  const rows = [
    { id: 'retired', profile: 'default', started_at: 100 },
    { id: 'fresh', profile: 'default', started_at: 200 }
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
  it('keeps the mainline and drops other sessions on the L1 profile', () => {
    const rows = [
      { id: 'main', profile: 'default' },
      { id: 'old', profile: 'default' },
      { id: 'agent', profile: 'l2-agenda' }
    ]

    expect(
      collectL1BypassSessionIds(rows, {
        agentProfiles: ['l2-agenda'],
        keepId: 'main',
        profile: 'default'
      })
    ).toEqual(['old'])
  })

  it('returns every L1-profile session when there is no keep id', () => {
    const rows = [
      { id: 'a', profile: 'default' },
      { id: 'b', profile: 'default' }
    ]

    expect(collectL1BypassSessionIds(rows, { profile: 'default' }).sort()).toEqual(['a', 'b'])
  })
})
