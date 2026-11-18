import { describe, expect, it } from 'vitest'

import { providerIdentity } from './model-status-label'

/**
 * provider 的**身份**（WP-PROVIDER-SOFTWARE-IDENTITY）。
 *
 * 网关（`bezel`）是**内部名**：用户的配置与屏上写的是**软件名**（`workbuddy`、
 * `qoder`…），机器标识只留在日志／doctor 行 id／路由值里（主人 11-17 18:5x：
 * 「bezel 是内部名称……provider 下面适配的是一个一个软件名，而不是 bezel」）。
 *
 * 网关那几行一行一个软件（`quotaProviders` 用 hub 名册重排过），`ui_key` 形如
 * `bezel::<软件 id>` ⇒ 那一段就是这一行的身份。
 */
describe('providerIdentity', () => {
  it('网关那几行：身份是**软件名**（ui_key 里 `::` 后面那一段）', () => {
    expect(providerIdentity({ slug: 'bezel', ui_key: 'bezel::workbuddy' })).toBe('workbuddy')
    expect(providerIdentity({ slug: 'bezel', ui_key: 'bezel::qoder' })).toBe('qoder')
    expect(providerIdentity({ slug: 'bezel', ui_key: 'bezel::opencode_api' })).toBe('opencode_api')
  })

  it('没有 ui_key 的网关行：模型 id 的前缀就是那家软件', () => {
    expect(providerIdentity({ slug: 'bezel' }, 'workbuddy/hy4free')).toBe('workbuddy')
    expect(providerIdentity({ slug: 'bezel' }, 'cursor/sonus')).toBe('cursor')
  })

  it('别家 provider：身份就是它的 slug', () => {
    expect(providerIdentity({ slug: 'openrouter' })).toBe('openrouter')
    expect(providerIdentity({ slug: 'openrouter' }, 'anthropic/claude-sonnet-4.5')).toBe('openrouter')
  })

  it('身份里不出现网关的机器标识', () => {
    const identity = providerIdentity({ slug: 'bezel', ui_key: 'bezel::workbuddy' })
    expect(identity).not.toBe('bezel')
    expect(identity).not.toContain('bezel')
  })
})
