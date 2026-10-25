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
    expect(workbuddy?.ui_key).toBe('aigw::WorkBuddy')
    expect(rows.some(row => row.name === 'Local Quota Hub')).toBe(false)
  })
})
