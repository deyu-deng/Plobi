import { describe, expect, it } from 'vitest'

import { providerIdentity } from './model-status-label'

/**
 * provider 的**身份**（WP-PROVIDER-SOFTWARE-IDENTITY，11-18 第三处收口）。
 *
 * 网关那几家软件**各自就是 provider**：行的 slug 就是软件名（`workbuddy`、
 * `qoder`…），`ui_key` 只做 Select 里的唯一键，不再靠 `::` 编码身份；也没有「一行
 * 代表整个网关、身份藏在模型 id 前缀里」这回事了。机器标识（传输别名）不出现在这
 * 一层（主人 11-17 18:5x）。
 */
describe('providerIdentity', () => {
  it('网关那几行：身份就是那一行自己的软件名', () => {
    expect(providerIdentity({ slug: 'workbuddy', ui_key: 'workbuddy' })).toBe('workbuddy')
    expect(providerIdentity({ slug: 'qoder', ui_key: 'qoder' })).toBe('qoder')
    expect(providerIdentity({ slug: 'opencode_api', ui_key: 'opencode_api' })).toBe('opencode_api')
  })

  it('ui_key 与 slug 不一致时也认 slug：ui_key 不编码身份', () => {
    expect(providerIdentity({ slug: 'workbuddy', ui_key: 'row-7' })).toBe('workbuddy')
  })

  it('别家 provider：身份就是它的 slug', () => {
    expect(providerIdentity({ slug: 'openrouter' })).toBe('openrouter')
    expect(providerIdentity({ slug: 'openrouter' }, 'anthropic/claude-sonnet-4.5')).toBe('openrouter')
  })

  it('身份里不出现网关的机器标识', () => {
    const identity = providerIdentity({ slug: 'workbuddy', ui_key: 'workbuddy' })
    expect(identity).not.toContain('bezel')
  })
})
