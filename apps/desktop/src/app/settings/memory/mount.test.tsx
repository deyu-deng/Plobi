/**
 * 真把设置页挂起来一次：`ConfigSettings`（不是单独挂 MemoryLedgers）在 memory 段下
 * 必须真的渲染出「记忆」那一块。这条补上"组件能画"与"源码里有那一行"之间的空档。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/i18n', async () => {
  const mod = await import('@/i18n/en')
  return { useI18n: () => ({ locale: 'en', t: mod.en }) }
})

vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(), () => undefined]
}))

vi.mock('../hooks/use-config-record', () => ({
  usePlobiConfigRecord: () => ({ data: { memory: { provider: 'mind' } }, isError: false, refetch: () => undefined }),
  setPlobiConfigCache: () => undefined
}))

vi.mock('@/plobi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/plobi')>()
  return {
    ...actual,
    getElevenLabsVoices: async () => [],
    getPlobiConfigSchema: async () => ({
      fields: { 'memory.provider': { type: 'string', description: '', title: 'Memory provider' } }
    }),
    savePlobiConfig: async () => ({})
  }
})

vi.mock('@/store/notifications', () => ({ notify: () => undefined, notifyError: () => undefined }))

vi.mock('../overlays/panel', () => ({ PanelEmpty: () => null }))

describe('ConfigSettings · memory 段', () => {
  it('渲染出记忆那一块（含三份 ledger 的容器）', async () => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async () => ({ agent: 'l1', scopes: [] }))
    }
    const { ConfigSettings } = await import('../config-settings')

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ConfigSettings activeSectionId="memory" importInputRef={{ current: null }} />
      </QueryClientProvider>
    )

    expect(await screen.findByTestId('memory-ledgers', undefined, { timeout: 8000 })).toBeTruthy()
  })
})
