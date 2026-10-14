import { describe, expect, it } from 'vitest'

import type { NetworkEgressBlock, NetworkEgressRecord, NetworkStatusSystemProxy } from '@/types/plobi'

import { appliedNote, buildSection, proxyStateText } from './network-settings'

const record = (egress: NetworkEgressBlock | null): NetworkEgressRecord =>
  ({ declared: true, network: { egress: egress ?? undefined } }) as NetworkEgressRecord

const COPY = {
  proxyDeclaredNotApplied: 'declared-but-not-ours',
  proxyNotOurs: 'not-taken-over',
  proxyOurs: 'we-hold-it',
  proxyUnsupported: 'windows-only'
}

const proxy = (over: Partial<NetworkStatusSystemProxy>): NetworkStatusSystemProxy => ({
  backup_exists: false,
  declared: false,
  ours: false,
  pac_path: 'D:/plobi/plobi-egress.pac',
  supported: true,
  ...over
})

describe('buildSection', () => {
  it('drops pieces the user left blank instead of writing empty containers', () => {
    const section = buildSection(
      [{ name: '', url: '' }],
      [{ match: '   ', via: 'proxy:ghost' }],
      record({ proxies: { old: { url: 'http://127.0.0.1:1' } }, rules: [{ match: '*', via: 'direct' }] })
    )

    // An empty `rules` list means "no opinion" to the parser, and a stray empty
    // `proxies: {}` reads later like something was forgotten about — so neither
    // is written.
    expect(section.egress?.proxies).toBeUndefined()
    expect(section.egress?.rules).toBeUndefined()
  })

  it('keeps the pieces this pane does not own', () => {
    const section = buildSection([{ name: 'home', url: 'http://127.0.0.1:7895' }], [], record(
      {
        engine: { binary: 'D:/mihomo/mihomo.exe', enabled: true, mixed_port: 7895 },
        mirrors: { npm: 'https://registry.npmmirror.com' }
      }
    ))

    expect(section.egress?.engine).toEqual({ binary: 'D:/mihomo/mihomo.exe', enabled: true, mixed_port: 7895 })
    expect(section.egress?.mirrors).toEqual({ npm: 'https://registry.npmmirror.com' })
    expect(section.egress?.proxies).toEqual({ home: { url: 'http://127.0.0.1:7895' } })
  })

  it('trims what it writes and defaults an unnamed via to direct', () => {
    const section = buildSection(
      [{ name: '  home  ', url: '  http://127.0.0.1:7895  ' }],
      [{ match: ' api.github.com ', via: '' }],
      record(null)
    )

    expect(section.egress?.proxies).toEqual({ home: { url: 'http://127.0.0.1:7895' } })
    expect(section.egress?.rules).toEqual([{ match: 'api.github.com', via: 'direct' }])
  })

  it('carries force_ipv4 only when the config actually has an opinion', () => {
    expect(buildSection([], [], record({})).force_ipv4).toBeUndefined()
    expect(buildSection([], [], record({}) as never).force_ipv4).toBeUndefined()

    const withFlag = { declared: true, network: { force_ipv4: false, egress: {} } } as NetworkEgressRecord
    expect(buildSection([], [], withFlag).force_ipv4).toBe(false)
  })

  it('carries the pieces the pane cannot render instead of writing them off', () => {
    // The save is a whole-section replace, so anything this pane does not draw has
    // to be handed back untouched. `system_proxy` is the one the takeover switch
    // owns, and the node pool is 46 entries the panel has no editor for — losing
    // either would look like the user's own Save had switched the VPN off.
    const source = record({
      engine: { enabled: true, mixed_port: 7895, dns: { enable: true, nameserver: ['223.5.5.5'] } },
      groups: { POOL: { include: ['n01 hk'], type: 'select' } },
      nodes: { 'n01 hk': { password_env: 'PLOBI_EGRESS_N01_HK_PASSWORD', type: 'ss' } },
      proxies: { home: { url: 'http://127.0.0.1:7895' } },
      rules: [{ match: '*', via: 'proxy:home' }],
      system_proxy: { enabled: true }
    })

    const section = buildSection([{ name: 'home', url: 'http://127.0.0.1:7895' }], [{ match: '*', via: 'proxy:home' }], source)

    expect(section.egress?.system_proxy).toEqual({ enabled: true })
    expect(section.egress?.nodes).toEqual({ 'n01 hk': { password_env: 'PLOBI_EGRESS_N01_HK_PASSWORD', type: 'ss' } })
    expect(section.egress?.groups).toEqual({ POOL: { include: ['n01 hk'], type: 'select' } })
    expect(section.egress?.engine).toMatchObject({ dns: { enable: true, nameserver: ['223.5.5.5'] } })
  })

  it('still lets a deletion land: the two lists it owns are not carried through', () => {
    const source = record({ proxies: { home: { url: 'http://127.0.0.1:7895' } }, rules: [{ match: '*', via: 'direct' }] })

    const section = buildSection([], [], source)

    expect(section.egress?.proxies).toBeUndefined()
    expect(section.egress?.rules).toBeUndefined()
  })
})

describe('proxyStateText', () => {
  it('keeps the intent and the registry as two different sentences', () => {
    expect(proxyStateText(COPY, proxy({ declared: true, ours: true }))).toBe('we-hold-it')
    // The state this whole slice worries about: the table asks for the takeover and
    // the machine does not show it. Saying "on" here would be the optimistic lie.
    expect(proxyStateText(COPY, proxy({ declared: true, ours: false }))).toBe('declared-but-not-ours')
    expect(proxyStateText(COPY, proxy({ declared: false, ours: false }))).toBe('not-taken-over')
  })

  it('never claims a takeover on a platform that cannot do it', () => {
    expect(proxyStateText(COPY, proxy({ declared: true, ours: true, supported: false }))).toBe('windows-only')
  })

  it('says nothing rather than "off" when the reading is missing', () => {
    expect(proxyStateText(COPY, undefined)).toBe('')
  })
})

describe('appliedNote', () => {
  it('says what supervision did, in the backend\'s own words', () => {
    expect(appliedNote('引擎', { state: 'running-ours', detail: 'restarted with the current table', probe: '204 via x' })).toBe(
      '引擎: running-ours — restarted with the current table (204 via x)'
    )
  })

  it('stays silent when the answer carries no engine half', () => {
    expect(appliedNote('引擎', undefined)).toBe('')
    // A refusal is still an answer: the sentence must reach the pane, not vanish.
    expect(appliedNote('引擎', { state: 'running-foreign', detail: 'reload refused' })).toBe('引擎: running-foreign — reload refused')
  })
})
