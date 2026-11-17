/**
 * 真把设置页挂起来一次：`ConfigSettings`（不是单独挂 MemoryLedgers）在 memory 段下
 * 必须真的渲染出「记忆」那一块；「写入边界」必须挂在 `memory.mind_write_auto_approve`
 * 那一行下面（用户 11-17 08:5x 拍），而不是跟着 `memory.provider` 走。
 *
 * 这条补上「组件能画」与「源码里有那一行」之间的空档——写边界的落点挪过一次：
 * 原先嵌在「个人记忆」卡里、跟着 provider 行，要往下滚十二行才见到。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// vitest 跑在 globals:false 下，testing-library 不会自己注册 afterEach cleanup ⇒
// 上一档的树会留在 document 里，第二个档查 `memory-ledgers` 会查到两个。
afterEach(() => cleanup())

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
      fields: {
        'memory.provider': { type: 'string', description: '', title: 'Memory provider' },
        'memory.mind_write_auto_approve': { type: 'boolean', description: '', title: 'Never ask' }
      }
    }),
    savePlobiConfig: async () => ({})
  }
})

vi.mock('@/store/notifications', () => ({ notify: () => undefined, notifyError: () => undefined }))

vi.mock('../overlays/panel', () => ({ PanelEmpty: () => null }))

/** 边界面板要的完整形状：少一个 `thresholds`，面板就会在渲染时抛。 */
const boundaryPayload = {
  breaker: { count: 0, threshold: 3, tripped: false, window_days: 7 },
  canon: 'Mind/wiki/memory/preferences.md',
  categories: {},
  policies: [],
  traces: [],
  thresholds: { k_reject: 3, n_adopt: 5, window_days: 7 }
}

/**
 * 按 path 分流的桩：这一屏现在同时会请求 `/api/memory/ledgers` 与 `/api/memory/boundary`，
 * 一律返回同一个对象会让后者拿到没有 `thresholds` 的形状、在渲染里抛错。
 */
function installApi() {
  const api = vi.fn(async ({ path }: { path: string }) => {
    if (path === '/api/memory/boundary') {
      return boundaryPayload
    }
    return { agent: 'l1', scopes: [] }
  })
  ;(window as unknown as { plobiDesktop: { api: typeof api } }).plobiDesktop = { api }
  return api
}

async function renderSettings() {
  const { ConfigSettings } = await import('../config-settings')
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConfigSettings activeSectionId="memory" importInputRef={{ current: null }} />
    </QueryClientProvider>
  )
}

describe('ConfigSettings · memory 段', () => {
  it('渲染出记忆那一块（含三份 ledger 的容器）', async () => {
    installApi()
    await renderSettings()

    expect(await screen.findByTestId('memory-ledgers', undefined, { timeout: 8000 })).toBeTruthy()
  })

  it('写入边界挂在「自动写入不用问」那一行下面，不再跟着 provider 走', async () => {
    installApi()
    await renderSettings()

    // 位置钉在这里：每一行的容器名是可算的（`setting-field-<key>`）。
    // schema 是异步取的，所以第一次查一定是 null——必须等它出现，不能同步 getElementById。
    const row = await waitFor(
      () => {
        const el = document.getElementById('setting-field-memory.mind_write_auto_approve')
        expect(el).toBeTruthy()
        return el as HTMLElement
      },
      { timeout: 8000 }
    )
    expect(await within(row).findByTestId('memory-boundary', undefined, { timeout: 8000 })).toBeTruthy()

    // 它也不许再出现在「个人记忆」那张卡里（那正是原先埋了十二行的地方）。
    const ledgers = await screen.findByTestId('memory-ledgers', undefined, { timeout: 8000 })
    expect(within(ledgers).queryByTestId('memory-boundary')).toBeNull()
  })
})
