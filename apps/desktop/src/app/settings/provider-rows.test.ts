import { beforeEach, describe, expect, it } from 'vitest'

import { setRuntimeI18nLocale } from '@/i18n/runtime'
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

    expect(rows.map(row => row.name)).toEqual(['Z.AI', 'Subscriptions'])
    expect(rows.find(row => row.name === 'Subscriptions')?.ui_key).toBe('aigw::Subscriptions')
  })

  it('the hub row speaks the interface language, not a hard-coded Chinese word', () => {
    // 那一行原来写死「订阅额度」——日文与繁中界面上也是中文。
    // 现在它跟界面语言走（默认英文；切到简中就是「订阅额度」）。
    setRuntimeI18nLocale('zh')

    try {
      const rows = mainProviderRows(catalog)

      expect(rows.map(row => row.name)).toEqual(['Z.AI', '订阅额度'])
    } finally {
      setRuntimeI18nLocale('en')
    }
  })

  it("never lets the gateway own name reach a row, even with no software connected", () => {
    // Measured 2026-10-28: `providers.aigw.name` read "Local Quota Hub" and showed up
    // on a surface without a per-software clone. The source is renamed; this pins the
    // renderer so the next such name cannot slip through either.
    const rows = mainProviderRows([{ models: ['workbuddy/hy3'], name: 'Local Quota Hub', slug: 'aigw' }])

    expect(rows.map(row => row.name)).not.toContain('Local Quota Hub')
    expect(rows.some(row => /quota hub/i.test(row.name))).toBe(false)
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
