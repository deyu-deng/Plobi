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

/**
 * 这一屏的全部字段，顺序就是设计上的分层顺序（**slot 声明表**里那一段的 `keys`：
 * 工作记忆 → 长时记忆 → 外脑（Exocortex） → 外部记忆提供商）。
 *
 * **11-26 更新（用户 11-25 23:3x）**：设置屏那几段从 slot 声明表派生 ⇒ 字段清单与
 * 分组都在 `app/shell/slot-registry.ts` 里（不在 `settings/constants.ts`）。
 * 这一份列表是**顺序契约**（钉住分层顺序不漂），故意留在测试里当期望值。
 *
 * 用 `vi.hoisted` 是因为下面的 `vi.mock` 工厂会被提升到文件顶部，普通 const 那时还没赋值。
 */
const memoryFields = vi.hoisted(() => [
  'context.engine',
  'compression.enabled',
  'compression.threshold',
  'compression.target_ratio',
  'compression.protect_last_n',
  'memory.memory_enabled',
  'memory.user_profile_enabled',
  'memory.write_approval',
  'memory.global_char_limit',
  'memory.agent_char_limit',
  'memory.user_char_limit',
  'memory.status_char_limit',
  'memory.conductor_status_char_limit',
  'memory.squeeze_pressure',
  'memory.nudge_interval',
  'memory.mind_enabled',
  'memory.mind_write_approval',
  'memory.mind_write_auto_approve',
  'memory.provider'
])

vi.mock('@/i18n', async () => {
  const mod = await import('@/i18n/en')
  return { useI18n: () => ({ locale: 'en', t: mod.en }) }
})

vi.mock('react-router-dom', () => ({
  useSearchParams: () => [new URLSearchParams(), () => undefined]
}))

vi.mock('../hooks/use-config-record', () => ({
  usePlobiConfigRecord: () => ({
    data: { memory: { mind_enabled: true, provider: '' } },
    isError: false,
    refetch: () => undefined
  }),
  setPlobiConfigCache: () => undefined
}))

vi.mock('@/plobi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/plobi')>()
  return {
    ...actual,
    getElevenLabsVoices: async () => [],
    getPlobiConfigSchema: async () => ({
      fields: Object.fromEntries(
        memoryFields.map(key => [key, { type: 'string', description: '', title: key }])
      )
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

  it('按四层分组渲染，组名与字段顺序都按定义排', async () => {
    installApi()
    await renderSettings()

    await waitFor(() => expect(document.querySelectorAll('[id^="setting-field-"]').length).toBe(memoryFields.length), {
      timeout: 8000
    })

    // 行顺序 === 设计上的分层顺序（`MEMORY_GROUPS` 各组 keys 的并集）。
    const ids = Array.from(document.querySelectorAll('[id^="setting-field-"]')).map(node => node.id)
    expect(ids).toEqual(memoryFields.map(key => `setting-field-${key}`))

    // 组名顺序：工作记忆 → 长时记忆 → 外脑 → 外部提供商（取自 en 目录；
    // 取不到就不给抬头，所以这条同时守着「四语键真的存在」）。
    const titles = ['Working memory', 'Long-term memory', 'Exocortex', 'External memory providers']
    const rendered = Array.from(document.querySelectorAll('h2'))
      .map(node => node.textContent)
      .filter(text => titles.includes(text ?? ''))
    expect(rendered).toEqual(titles)
  })

  it('个人记忆账跟着长时记忆走，写边界跟着 Mind 走，都不在外部提供商那一行', async () => {
    installApi()
    await renderSettings()

    const rowOf = async (key: string) =>
      waitFor(
        () => {
          const el = document.getElementById(`setting-field-${key}`)
          expect(el).toBeTruthy()
          return el as HTMLElement
        },
        { timeout: 8000 }
      )

    const ledgersRow = await rowOf('memory.memory_enabled')
    expect(await within(ledgersRow).findByTestId('memory-ledgers', undefined, { timeout: 8000 })).toBeTruthy()

    const mindRow = await rowOf('memory.mind_write_auto_approve')
    expect(await within(mindRow).findByTestId('memory-boundary', undefined, { timeout: 8000 })).toBeTruthy()

    // 外部提供商那一行只剩连接状态：它不属于长时记忆，也不该再托管任何账。
    const providerRow = await rowOf('memory.provider')
    expect(within(providerRow).queryByTestId('memory-ledgers')).toBeNull()
    expect(within(providerRow).queryByTestId('memory-boundary')).toBeNull()
  })
})
