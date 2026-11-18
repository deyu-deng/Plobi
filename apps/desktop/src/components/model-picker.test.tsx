import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connectedDesktopApps, markConnected } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { ModelPickerDialog } from './model-picker'

// cmdk / Radix reach for these; jsdom doesn't ship them.
class FakeResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}
globalThis.ResizeObserver ??= FakeResizeObserver as unknown as typeof ResizeObserver

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

// Two legs of one software, one row each (11-18)：后端给的就是一家软件一行，行的
// `slug` 是这家软件自己的名字。两条腿同名（同属一个品牌），所以任何拿 `name` 当 key
// 的写法都会撞——撞了 React 就卸载一棵子树再挂另一棵，模型框每次渲染都重新问一遍
// 网关，那就是 2026-10-26 「一打开模型选择框整个聊天框就一直闪」。
const catalog: ModelOptionProvider[] = [
  { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
  { models: ['workbuddy/hy3'], name: 'WorkBuddy', slug: 'workbuddy' },
  { models: ['workbuddy_api/hy3'], name: 'WorkBuddy', slug: 'workbuddy_api' }
]

function gatewayReturning(providers: ModelOptionProvider[]) {
  return {
    request: vi.fn(async () => ({ providers }))
  } as never
}

async function renderPicker(
  providers: ModelOptionProvider[],
  onSelect: (selection: { provider: string; model: string }) => void = () => {}
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const gw = gatewayReturning(providers)
  render(
    <QueryClientProvider client={client}>
      <ModelPickerDialog
        contentClassName=""
        currentModel="workbuddy/hy3"
        currentProvider="workbuddy"
        gw={gw}
        onOpenChange={() => {}}
        onSelect={onSelect}
        open
        sessionId="s-1"
      />
    </QueryClientProvider>
  )
  // Radix DialogContent renders through a portal, so the rows live on
  // document.body rather than in the container testing-library returned. Waiting on
  // the rows (not on the header copy, which already names the current model) is what
  // proves the catalog actually landed before we count groups.
  await waitFor(() => expect(document.querySelectorAll('[cmdk-item]').length).toBeGreaterThan(0))
  return document.body
}

describe('ModelPickerDialog group identity', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'WorkBuddy' })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders both same-slug hub legs without a duplicate React key', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})

    const root = await renderPicker(catalog)

    const headings = [...root.querySelectorAll('[cmdk-group-heading]')]
    const workbuddy = headings.filter(h => (h.textContent ?? '').includes('WorkBuddy'))

    expect(workbuddy).toHaveLength(2)
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/two children with the same key/)
  })

  it('keys the model rows by the row identity, not the shared hub slug', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})

    const root = await renderPicker(catalog)

    // Every row is a cmdk item; two legs × one model = two rows, and they must not
    // collide on cmdk's own `value` identity either.
    const items = [...root.querySelectorAll('[cmdk-item]')]

    expect(items).toHaveLength(3) // zai's glm-5.2 + the two hub legs
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/two children with the same key/)
  })
})

it('prints the model without its upstream provider prefix', async () => {
  // 用户 11-14：「UI 上不要带 workbuddy」。上游返回的是 `provider/model` 复合 id，
  // 那是路由用的字符串，不是给人看的名字——provider 已经单独占一行了。
  renderPicker(catalog)

  await waitFor(() => expect(document.querySelectorAll('[cmdk-item]')).toHaveLength(3))

  const rows = Array.from(document.querySelectorAll('[cmdk-item]')).map(el => el.textContent ?? '')
  expect(rows.join(' | ')).not.toContain('workbuddy/')
})

it('a picked hub leg reports the software name, not the hub slug', async () => {
  // 网关（`bezel`）是内部名：选中的那一行交出去的是**软件名**（主人 11-17 18:5x：
  // 「bezel 是内部名称……provider 下面适配的是一个一个软件名，而不是 bezel」）。
  // 这一条盯的是模型框自己的写侧——聊天面板那一处由
  // `model-menu-panel.waiting.test.tsx` 盯着。
  //
  // 上面那两条测试没有 await `renderPicker`，它们的 DOM 会留在页面里；模型框的
  // 行是从 `document.body` 里查的，所以这里先清一次——否则点到的是上一次的
  // 那个模型框（它的回调是默认的空函数）。
  cleanup()
  $connectedDesktopApps.set({})
  markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })

  const onSelect = vi.fn()

  // 后端只列已授权的软件，所以这里也只给那一行。
  await renderPicker(catalog.filter(row => row.slug !== 'workbuddy_api'), onSelect)

  const items = Array.from(document.querySelectorAll('[cmdk-item]'))
  expect(items).toHaveLength(2) // zai's glm-5.2 + the one connected hub leg

  // items[1] is the WorkBuddy leg.
  fireEvent.click(items[1])

  expect(onSelect).toHaveBeenCalledWith({ provider: 'workbuddy', model: 'workbuddy/hy3' })
  // The hub's machine id never leaves the picker.
  expect(JSON.stringify(onSelect.mock.calls)).not.toContain('bezel')
})
