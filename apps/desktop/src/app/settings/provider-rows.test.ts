import { beforeEach, describe, expect, it } from 'vitest'

import { $connectedDesktopApps, markConnected } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { mainProviderRows } from './provider-rows'

const catalog: ModelOptionProvider[] = [
  { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
  {
    models: ['workbuddy/hy3', 'antigravity/gemini-3-pro'],
    name: 'Local Quota Hub',
    slug: 'aigw'
  }
]

describe('mainProviderRows', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('never shows the internal hub name as a provider', () => {
    const rows = mainProviderRows(catalog)

    expect(rows.map(row => row.name)).toEqual(['Z.AI', '订阅额度'])
    expect(rows.find(row => row.name === '订阅额度')?.ui_key).toBe('aigw::订阅额度')
  })

  it('replaces the hub with one row per connected software and keeps the real routing slug', () => {
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })

    const rows = mainProviderRows(catalog)
    const workbuddy = rows.find(row => row.name === 'WorkBuddy')

    expect(workbuddy?.slug).toBe('aigw')
    // The row's identity is the hub APP ID, not its display name: two legs of one
    // software humanize to the same brand by design, and a name-based key made the
    // picker groups collide (2026-10-26 flicker).
    expect(workbuddy?.ui_key).toBe('aigw::workbuddy')
    expect(rows.some(row => row.name === 'Local Quota Hub')).toBe(false)
  })

  it('keeps two same-named legs apart on the Settings surface too', () => {
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'WorkBuddy' })

    // The backend offers both legs, so both rows survive and must stay keyable.
    const bothLegs: ModelOptionProvider[] = [
      { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
      {
        models: ['workbuddy/hy3', 'workbuddy_api/hy3', 'antigravity/gemini-3-pro'],
        name: 'Local Quota Hub',
        slug: 'aigw'
      }
    ]

    const rows = mainProviderRows(bothLegs).filter(row => row.slug === 'aigw')

    expect(rows).toHaveLength(2)
    expect(rows.map(row => row.name)).toEqual(['WorkBuddy', 'WorkBuddy'])
    expect(new Set(rows.map(row => row.ui_key))).toEqual(new Set(['aigw::workbuddy', 'aigw::workbuddy_api']))
  })
})
