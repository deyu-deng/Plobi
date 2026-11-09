import { describe, expect, it } from 'vitest'

import { setRuntimeI18nLocale } from '@/i18n/runtime'
import type { ModelOptionProvider } from '@/types/plobi'

import { humanizeProviderName, humanizeProviders, isMockModelId } from './model-provider-label'

function provider(overrides: Partial<ModelOptionProvider> & { slug: string }): ModelOptionProvider {
  return { authenticated: true, models: [], name: overrides.slug, total_models: 0, ...overrides }
}

describe('humanizeProviderName', () => {
  it('maps aigw-aggregated software to its brand (slug or name match)', () => {
    expect(humanizeProviderName(provider({ slug: 'workbuddy', name: 'anything' }))).toBe('WorkBuddy')
    expect(humanizeProviderName(provider({ slug: 'whatever', name: 'AIGW · Antigravity' }))).toBe('Antigravity')
    expect(humanizeProviderName(provider({ slug: 'qoder', name: 'Qoder' }))).toBe('Qoder')
  })

  it('maps direct-API vendors to their public names', () => {
    expect(humanizeProviderName(provider({ slug: 'deepseek', name: 'CUSTOM ENDPOINT' }))).toBe('DeepSeek')
    expect(humanizeProviderName(provider({ slug: 'minimax-official', name: 'CUSTOM ENDPOINT' }))).toBe('MiniMax')
    expect(humanizeProviderName(provider({ slug: 'zhipu', name: 'CUSTOM ENDPOINT' }))).toBe('智谱 GLM')
  })

  it('strips internal vocabulary when the gateway sends an unknown name', () => {
    expect(humanizeProviderName(provider({ slug: 'proxy-x', name: 'AIGW Custom Endpoint (proxy-x)' }))).toBe(
      '(proxy-x)'
    )
  })

  it('falls back to the slug so groups are never untitled', () => {
    expect(humanizeProviderName(provider({ slug: 'my-gateway', name: 'AIGW' }))).toBe('My Gateway')
  })

  it('the quota hub folds into the catalog word — in every interface language', () => {
    // 这一支原来写死「订阅额度」（日文与繁中界面上也是中文），而且那条正则里
    // 混进两枚控制字符（`39020bf8` 带进来的），英文那一半永远不命中 ⇒
    // 「Local Quota Hub」会原样上屏。现在两半都活了，文案走 i18n 目录。
    expect(humanizeProviderName(provider({ slug: 'aigw', name: 'Local Quota Hub' }))).toBe('Subscriptions')
    expect(humanizeProviderName(provider({ slug: 'aigw', name: '本地额度网关' }))).toBe('Subscriptions')

    setRuntimeI18nLocale('ja')

    try {
      expect(humanizeProviderName(provider({ slug: 'aigw', name: 'Local Quota Hub' }))).toBe('サブスクリプション')
    } finally {
      setRuntimeI18nLocale('en')
    }
  })
})

describe('humanizeProviders', () => {
  it('drops the mock provider and mock/ models, keeps slugs stable', () => {
    const out = humanizeProviders([
      provider({ slug: 'mock', name: 'Mock', models: ['mock/echo', 'mock/fail'] }),
      provider({ slug: 'deepseek', name: 'CUSTOM ENDPOINT', models: ['deepseek/deepseek-chat', 'mock/echo'] }),
      provider({ slug: 'workbuddy', name: 'AIGW', models: ['workbuddy/kimi-k3'] }),
      provider({ slug: 'empty-after-filter', models: ['mock/static'] })
    ])

    expect(out.map(row => row.slug)).toEqual(['deepseek', 'workbuddy'])
    expect(out[0]?.name).toBe('DeepSeek')
    expect(out[0]?.models).toEqual(['deepseek/deepseek-chat'])
    expect(out[1]?.name).toBe('WorkBuddy')
  })

  it('keeps the moa virtual provider', () => {
    const out = humanizeProviders([provider({ slug: 'moa', name: 'MoA', models: ['moa/mixture'] })])

    expect(out).toHaveLength(1)
    expect(out[0]?.slug).toBe('moa')
  })
})

describe('isMockModelId', () => {
  it('flags only the mock/ namespace', () => {
    expect(isMockModelId('mock/echo')).toBe(true)
    expect(isMockModelId(' mock/dead ')).toBe(true)
    expect(isMockModelId('deepseek/deepseek-chat')).toBe(false)
    expect(isMockModelId('mockery/real')).toBe(false)
  })
})
