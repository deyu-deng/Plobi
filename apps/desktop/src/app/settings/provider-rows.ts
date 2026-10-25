import type { ModelOptionProvider } from '@/types/plobi'
import { quotaProviders } from '@/store/desktop-quotas'

import { humanizeProviderRows } from '../shell/model-provider-label'

/**
 * Provider rows for the main-model picker.
 *
 * The quota hub (`aigw`) is routing plumbing, not a vendor the user picks. When
 * the shell has pushed at least one connected quota app, replace the hub row
 * with one row per software (`WorkBuddy`, `Antigravity`) — all still routing to
 * `aigw` but carrying a unique `ui_key` so the Select can tell them apart.
 * Before the first frame (or when no app is connected) keep one neutral
 * fallback row instead of showing the internal hub name.
 */
export function mainProviderRows(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  const rows = quotaProviders(providers)
  const hasQuotaGroups = rows.some(row => (row.slug || '').toLowerCase() === 'aigw')
  const hub = providers.find(provider => (provider.slug || '').toLowerCase() === 'aigw')
  const withFallback =
    !hasQuotaGroups && hub ? [...rows, { ...hub, name: '订阅额度' }] : rows

  return humanizeProviderRows(withFallback).map(row => ({
    ...row,
    ui_key:
      row.ui_key ??
      ((row.slug || '').toLowerCase() === 'aigw'
        ? `aigw::${row.name || 'hub'}`
        : row.slug)
  }))
}
