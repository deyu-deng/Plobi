/**
 * 设置里那一页「记忆」：三份按条列出、忘掉两下才真删且带走回执；
 * 下面再接一段「写入边界」（`WP-MIND-SILENCE-VISIBLE`）。
 *
 * 现核那两条链：
 * - `GET /api/memory/ledgers` → 逐条渲染 → 第一下只上膛 → 第二下
 *   `POST /api/memory/forget`，页面必须换成服务器回来的新快照（不是本地先删）。
 * - `GET /api/memory/boundary` → 熔断／每类连采／已生效政策／最近 trace。
 *   两个「连采」不是一回事：上面那行是 L0 汇报信任（门槛 8），下面每类是
 *   L1 写入信任（门槛 5，由后端给），所以断言里必须能分辨这两个数。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryLedgers } from './ledgers'

// 这一档跑在 vitest 的 globals:false 下，testing-library 不会自己注册 afterEach
// cleanup ⇒ 不手动清的话 `screen` 会跨档累加（新加的边界面板每个档都渲染一次，
// 到第三档就有三个 `data-testid="memory-boundary"`）。
afterEach(() => cleanup())

vi.mock('@/i18n', () => ({
  useI18n: () => ({ locale: 'zh', t: {} })
}))

const snapshot = {
  agent: 'l2-course-x',
  scopes: [
    { chars: 6, entries: [{ index: 0, text: '共用甲' }, { index: 1, text: '共用乙' }], key: 'global', label: '共用纪律', limit: 2200, present: true, streak: 0, threshold: 8 },
    { chars: 0, entries: [], key: 'user', label: '你的画像', limit: 1375, present: false, streak: 0, threshold: 8 },
    { chars: 8, entries: [{ index: 0, text: '周三有实验' }], key: 'memory', label: '这个 Agent 自己的记忆', limit: 2200, present: true, streak: 3, threshold: 8 }
  ]
}

const boundary = {
  breaker: { count: 0, threshold: 3, tripped: false, window_days: 7 },
  canon: 'Mind/wiki/memory/preferences.md',
  categories: {
    notes: { adopted: 2, category: 'notes', edited: 0, last_ts: 1, recent: [], rejected: 0, streak: 2, streak_source: 'consent_events' },
    wiki: { adopted: 5, category: 'wiki', edited: 0, last_ts: 1, recent: [], rejected: 1, streak: 3, streak_source: 'consent_events' }
  },
  db: '/tmp/consent.db',
  policies: [
    { category: 'wiki', evidence: ['cev_1'], key: 'promote:wiki', state: 'active', text: '类别 wiki 的写入：你连着点头 5 次，以后这类我不再问。' }
  ],
  thresholds: { k_reject: 3, n_adopt: 5, window_days: 7 },
  traces: [
    { category: 'wiki', id: 'trc_1', reason: '够格静默（五条全过）', silent: true, target: 'Mind/wiki/daily/2026-11-17.md', trace: { policy_keys: ['promote:wiki'] }, ts: 1 }
  ]
}

function installApi(overrides: { boundary?: unknown; boundaryFails?: boolean } = {}) {
  const api = vi.fn(async ({ path }: { path: string }) => {
    if (path === '/api/memory/ledgers') {
      return snapshot
    }
    if (path === '/api/memory/boundary') {
      if (overrides.boundaryFails) {
        throw new Error('404 Not Found')
      }
      return overrides.boundary ?? boundary
    }
    return { ...snapshot, ok: true, removed: '周三有实验', scopes: [{ ...snapshot.scopes[2], chars: 0, entries: [] }] }
  })
  ;(window as unknown as { plobiDesktop: { api: typeof api } }).plobiDesktop = { api }
  return api
}

// 两档共用同一个默认 mock（顶层 beforeEach 才会被两个 describe 都继承；
// 写在某个 describe 里的话，另一档会拿到上一档残留的 window.plobiDesktop）。
beforeEach(() => {
  installApi()
})

describe('MemoryLedgers', () => {
  it('按条列出三份记忆，并显示已用/上限与信任账', async () => {
    render(<MemoryLedgers />)

    expect(await screen.findByText('共用甲')).toBeTruthy()
    expect(screen.getByText('共用乙')).toBeTruthy()
    expect(screen.getByText('周三有实验')).toBeTruthy()
    expect(screen.getByText('6/2200')).toBeTruthy()
    // L0 那一个数必须自报口径，否则会被读成「记忆写入攒到几了」
    expect(screen.getByText('连采 3/8（汇报）')).toBeTruthy()
    expect(screen.getAllByText('还没记住什么').length).toBe(1)
  })

  it('忘掉要两下：第一下只上膛，第二下才真删并用新快照替换', async () => {
    // 档间不自动 cleanup：断言"不存在"必须 within 当前容器
    const view = render(<MemoryLedgers />)
    const box = within(view.container)
    await box.findByText('周三有实验')

    const api = (window as unknown as { plobiDesktop: { api: ReturnType<typeof vi.fn> } }).plobiDesktop.api
    fireEvent.click(box.getAllByText('忘掉')[2])
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/forget' }))

    fireEvent.click(box.getByText('确认忘掉'))

    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({ method: 'POST', path: '/api/memory/forget' })), { timeout: 3000 })
    await waitFor(() => expect(box.queryByText('周三有实验')).toBeNull(), { timeout: 3000 })
  })

  it('后端还是旧进程（404）时说的是「重启后可用」，不是笼统的读不到', async () => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async () => {
        throw new Error('404 Not Found')
      })
    }
    render(<MemoryLedgers />)

    expect(await screen.findByText('后端还是旧进程，重启 Plobi 后这一页就能读。')).toBeTruthy()
  })
})

describe('MemoryBoundary', () => {
  it('每类一行：写入连采用 5 的分母，与上面那行 8 分得开', async () => {
    render(<MemoryLedgers />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('连采 3/5（写入）')).toBeTruthy()
    expect(within(panel).getByText('连采 2/5（写入）')).toBeTruthy()
    expect(within(panel).getByText('驳回 1')).toBeTruthy()
    expect(within(panel).getByText('已授权')).toBeTruthy()
    expect(within(panel).getByText('未授权')).toBeTruthy()
  })

  it('已生效政策给原文，并说清怎么收回', async () => {
    render(<MemoryLedgers />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('类别 wiki 的写入：你连着点头 5 次，以后这类我不再问。')).toBeTruthy()
    expect(within(panel).getByText('删掉这一行，这一类就回到每次都问')).toBeTruthy()
  })

  it('最近一次静默能拼出「写进哪、哪一类、凭哪条政策」', async () => {
    render(<MemoryLedgers />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('写进 Mind/wiki/daily/2026-11-17.md（wiki 类；凭 promote:wiki）')).toBeTruthy()
  })

  it('熔断时最上方出一条告警，写明几次、几天', async () => {
    installApi({ boundary: { ...boundary, breaker: { count: 3, threshold: 3, tripped: true, window_days: 7 } } })
    render(<MemoryLedgers />)

    const panel = await screen.findByTestId('memory-boundary')
    const alert = within(panel).getByRole('alert')
    expect(alert.textContent).toContain('7 天内驳回 3/3 次')
  })

  it('全新安装（没有表态史）时说的是「还没有值得学的」，不留白', async () => {
    installApi({ boundary: { ...boundary, categories: {}, policies: [], traces: [] } })
    render(<MemoryLedgers />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('还没有值得学的——批准或驳回几次之后，这里的读数才会长出来')).toBeTruthy()
    expect(within(panel).getByText('还没有静默过')).toBeTruthy()
  })

  it('老后端没有这个口时整块不渲染，原有三份记忆照常', async () => {
    installApi({ boundaryFails: true })
    render(<MemoryLedgers />)

    expect(await screen.findByText('共用甲')).toBeTruthy()
    expect(screen.queryByTestId('memory-boundary')).toBeNull()
    // 不许因为边界面板读不到就冒一个红字出来
    expect(screen.queryByText('现在读不到记忆')).toBeNull()
  })
})
