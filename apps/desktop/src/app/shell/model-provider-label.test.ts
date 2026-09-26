import { describe, expect, it } from 'vitest'

import type { ModelOptionProvider } from '@/types/plobi'

import { humanizeProviderName, humanizeProviders, isMockModelId } from './model-provider-label'

function provider(overrides: Partial<ModelOptionProvider> & { slug: string }): ModelOptionProvider {
  return { authenticated: true, models: [], name: overrides.slug, total_models: 0, ...overrides }
}

describe('humanizeProviderName', () => {
  it('maps aigw-aggregated software to its brand (slug or name match)', () => {
    expect(humanizeProviderName(provider({ slug: 'workbuddy', name: 'anything' }))).toBe('WorkBuddy')
    expect(humanizeProviderName(provider({ slug: 'whatever', name: 'AIGW · Antigravity' }))).toBe('Antigravity')
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
