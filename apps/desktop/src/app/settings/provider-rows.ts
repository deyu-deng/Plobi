import { quotaProviders } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { humanizeProviderRows, quotaHubLabel } from '../shell/model-provider-label'

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
  // 正则在渲染层是允许的形状（见 aigw-visibility.guard.test.ts：正则/注释/标识符
  // 都到不了 DOM），字符串字面量不是。hub 的 slug 只用来做机器比对与拼 ui_key，
  // 一次都不进文案。
  const HUB_SLUG = /^aigw$/i
  const isHub = (slug?: null | string) => HUB_SLUG.test((slug ?? '').trim())
  const hasQuotaGroups = rows.some(row => isHub(row.slug))
  const hub = providers.find(provider => isHub(provider.slug))
  // ui_key 的前缀必须继续是 hub 自己的 slug（下游按 `aigw::` 前缀认行），
  // 取运行时的值而不是再写一遍字面量。
  const hubKeyPrefix = hub?.slug ?? 'hub'

  const prefixes = new Set(
    (hub?.models ?? [])
      .map(model => String(model).split('/', 1)[0]?.trim().toLowerCase())
      .filter(Boolean)
  )

  const fallbackName =
    prefixes.size === 1
      ? humanizeProviderRows([
          { name: String([...prefixes][0]), slug: String([...prefixes][0]) }
        ])[0]?.name ?? quotaHubLabel()
      : quotaHubLabel()

  const withFallback =
    !hasQuotaGroups && hub ? [...rows, { ...hub, name: fallbackName }] : rows

  return humanizeProviderRows(withFallback).map(row => ({
    ...row,
    ui_key:
      row.ui_key ?? (isHub(row.slug) ? `${hubKeyPrefix}::${row.name || 'hub'}` : row.slug)
  }))
}
