import { describe, expect, it } from 'vitest'

import type { NetworkEgressBlock, NetworkEgressRecord } from '@/types/plobi'

import { appliedNote, buildSection } from './network-settings'

const record = (egress: NetworkEgressBlock | null): NetworkEgressRecord =>
  ({ declared: true, network: { egress: egress ?? undefined } }) as NetworkEgressRecord

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
