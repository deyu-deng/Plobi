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
  const prefixes = new Set(
    (hub?.models ?? [])
      .map(model => String(model).split('/', 1)[0]?.trim().toLowerCase())
      .filter(Boolean)
  )
  const fallbackName =
    prefixes.size === 1
      ? humanizeProviderRows([
          { name: String([...prefixes][0]), slug: String([...prefixes][0]) }
        ])[0]?.name ?? '订阅额度'
      : '订阅额度'
  const withFallback =
    !hasQuotaGroups && hub ? [...rows, { ...hub, name: fallbackName }] : rows

  return humanizeProviderRows(withFallback).map(row => ({
    ...row,
    ui_key:
      row.ui_key ??
      ((row.slug || '').toLowerCase() === 'aigw'
        ? `aigw::${row.name || 'hub'}`
        : row.slug)
  }))
}
