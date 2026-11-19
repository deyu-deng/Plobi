import { quotaProviders } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { humanizeProviderRows } from '../shell/model-provider-label'

/**
 * Provider rows for the main-model picker.
 *
 * 11-18 第三处之后，网关那几家软件各自就是 provider：后端一行一家，slug 就是软件
 * 名，`quotaProviders` 只按网关判决收窄，不再把一行拆成多行，`ui_key` 也不再是
 * `bezel::<软件>`，行自己的 slug 就是它的身份。
 *
 * 11-19：那行「一帧还没到」的兜底行**删了**。它的 slug 是 `hub`，一个用户点下去
 * 只会在配置里写出 `provider: hub` 的假身份——后端根本没有这个 provider。清单空
 * 就是空，选择器宁可以空着等第一帧，也不造假行。
 */
export function mainProviderRows(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  return humanizeProviderRows(quotaProviders(providers)).map(row => ({
    ...row,
    ui_key: row.ui_key ?? row.slug
  }))
}
