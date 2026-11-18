import { isHubSoftwareRow, quotaProviders } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { humanizeProviderRows, quotaHubLabel } from '../shell/model-provider-label'

/**
 * Provider rows for the main-model picker.
 *
 * 11-18 第三处之后，网关那几家软件各自就是 provider：后端一行一家，slug 就是软件
 * 名，`quotaProviders` 只按网关判决收窄，不再把一行拆成多行，`ui_key` 也不再是
 * `bezel::<软件>`，行自己的 slug 就是它的身份。
 *
 * 唯一保留的分支是「一帧都还没到」：这时清单里没有网关软件行，给一行中性名，免得
 * 选择器空着。
 */
export function mainProviderRows(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  const rows = quotaProviders(providers)

  const withFallback: ModelOptionProvider[] = rows.some(isHubSoftwareRow)
    ? rows
    : [...rows, { models: [], name: quotaHubLabel(), slug: 'hub', ui_key: 'hub' }]

  return humanizeProviderRows(withFallback).map(row => ({
    ...row,
    ui_key: row.ui_key ?? row.slug
  }))
}
