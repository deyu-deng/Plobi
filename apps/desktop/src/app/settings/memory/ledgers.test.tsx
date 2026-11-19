/**
 * 设置里那一页「记忆」：一份一行、条目进抽屉、忘掉两下才真删且带走回执。
 * 另有一块「写入边界」（`WP-MIND-SILENCE-VISIBLE`），**不再嵌在这一页里**——
 * 它挂在 `memory.mind_write_auto_approve` 那一行下面，位置由 `mount.test.tsx`
 * 钉住；本档只测它自己画得对不对，不测它被塞在哪。
 *
 * 现核那两条链：
 * - `GET /api/memory/ledgers` → 三行读数（不铺条目）→ 点开那一行进抽屉 → 逐条渲染 →
 *   第一下只上膛 → 第二下 `POST /api/memory/forget`，页面必须换成服务器回来的新快照
 *   （不是本地先删）。
 * - `GET /api/memory/boundary` → 熔断／每类连采／已生效政策／最近 trace。
 *   两个「连采」不是一回事：上面那行是 L0 汇报信任（门槛 8），下面每类是
 *   L1 写入信任（门槛 5，由后端给），所以断言里必须能分辨这两个数。
 *
 * 用户 11-19 21:2x 的两句是本档的判据来源：条目摊在行上「太丑」⇒ 折叠行进抽屉；
 * 「有些语言没有产品化」⇒ 四份记忆的名字由前端按 key 译，后端那个中文 `label`
 * 在英/日里**一个字都不许上屏**（快照里故意给中文 label 来钉这一条）。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryBoundary, MemoryLedgers } from './ledgers'

// 抽屉（`Sheet`）内部要读 `t.common.close`，locale 要能逐档换 ⇒ 用可变对象，
// 不是每档重新 mock 一次模块。
const i18nState = vi.hoisted(() => ({ locale: 'zh' }))

// 这一档跑在 vitest 的 globals:false 下，testing-library 不会自己注册 afterEach
// cleanup ⇒ 不手动清的话 `screen` 会跨档累加（新加的边界面板每个档都渲染一次，
// 到第三档就有三个 `data-testid="memory-boundary"`）。
afterEach(() => {
  cleanup()
  i18nState.locale = 'zh'
})

vi.mock('@/i18n', () => ({
  useI18n: () => ({ locale: i18nState.locale, t: { common: { close: 'Close' } } })
}))

// 抽屉里那颗「看等你点头的」按钮会跳去写脑待批那一面，这档不验它。
vi.mock('@/store/layout', () => ({ openMonitorFace: () => undefined }))

const snapshot = {
  agent: 'l2-course-x',
  scopes: [
    {
      chars: 6,
      entries: [
        { index: 0, text: '共用甲' },
        { index: 1, text: '共用乙' }
      ],
      key: 'global',
      label: '共用纪律',
      limit: 2200,
      present: true,
      streak: 0,
      threshold: 8
    },
    { chars: 0, entries: [], key: 'user', label: '你的画像', limit: 1375, present: false, streak: 0, threshold: 8 },
    {
      chars: 8,
      entries: [{ index: 0, text: '周三有实验' }],
      key: 'memory',
      label: '这个 Agent 自己的记忆',
      limit: 2200,
      present: true,
      streak: 3,
      threshold: 8
    }
  ]
}

const boundary = {
  breaker: { count: 0, threshold: 3, tripped: false, window_days: 7 },
  canon: 'Mind/wiki/memory/preferences.md',
  categories: {
    notes: {
      adopted: 2,
      category: 'notes',
      edited: 0,
      last_ts: 1,
      recent: [],
      rejected: 0,
      streak: 2,
      streak_source: 'consent_events'
    },
    wiki: {
      adopted: 5,
      category: 'wiki',
      edited: 0,
      last_ts: 1,
      recent: [],
      rejected: 1,
      streak: 3,
      streak_source: 'consent_events'
    }
  },
  db: '/tmp/consent.db',
  policies: [
    {
      category: 'wiki',
      evidence: ['cev_1'],
      key: 'promote:wiki',
      state: 'active',
      text: '类别 wiki 的写入：你连着点头 5 次，以后这类我不再问。'
    }
  ],
  thresholds: { k_reject: 3, n_adopt: 5, window_days: 7 },
  traces: [
    {
      category: 'wiki',
      id: 'trc_1',
      reason: '够格静默（五条全过）',
      silent: true,
      target: 'Mind/wiki/daily/2026-11-17.md',
      trace: { policy_keys: ['promote:wiki'] },
      ts: 1
    }
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

/** 点开某一份的抽屉（抽屉挂在 body 的 portal 上，用 `screen` 查）。 */
async function openScope(key: string) {
  const row = await screen.findByTestId(`memory-scope-${key}`)
  fireEvent.click(within(row).getByRole('button'))

  return screen.findByTestId('memory-drawer')
}

describe('MemoryLedgers', () => {
  it('一份一行：行上只有名字与读数，条目一条都不铺在行上', async () => {
    render(<MemoryLedgers />)

    // 四个名字由前端给（后端 label 只作兜底），读数带条数。
    expect(await screen.findByText('全局记忆')).toBeTruthy()
    expect(screen.getByText('用户画像')).toBeTruthy()
    // 属于某个 Agent 的那两份写的是那个 Agent 的名字，不是无主的「这个 Agent」
    // （用户 11-19 21:5x：「我发现『这个 Agent 的记忆』是只哪一个 Agent 的记忆呢？」）。
    expect(screen.getByText('l2-course-x 的记忆')).toBeTruthy()
    // 解释句不上屏：名字已带归属，再来一句「这几份记忆属于 X」只是多读一行
    // （用户 11-19 22:2x：「一些元注释也不需要上屏幕」「保持简洁优雅」）。
    expect(screen.queryByText(/属于/)).toBeNull()
    expect(screen.getByText('6/2200 · 2 条')).toBeTruthy()
    expect(screen.getByText('0/1375 · 0 条')).toBeTruthy()

    // 用户 11-19 的原话是「这样展开太丑了」：没点开之前，条目一个字都不在屏上。
    expect(screen.queryByText('共用甲')).toBeNull()
    expect(screen.queryByText('周三有实验')).toBeNull()
    expect(screen.queryByTestId('memory-drawer')).toBeNull()
  })

  it('点开那一行才进抽屉：逐条列，L0 那个数在抽屉抬头自报口径', async () => {
    render(<MemoryLedgers />)

    const drawer = await openScope('memory')
    expect(within(drawer).getByText('周三有实验')).toBeTruthy()
    // L0 那一个数必须自报口径，否则会被读成「记忆写入攒到几了」。它在抽屉抬头
    // （不在滚动区里），所以这里按整屏查。
    expect(screen.getByText('汇报采纳 3/8')).toBeTruthy()

    // 同一份里的条目只在这一份的抽屉里；别份的条目不在。
    expect(within(drawer).queryByText('共用甲')).toBeNull()
  })

  it('抽屉抬头点名这一份属于谁，并且内容从窗口顶栏下面开始（不压系统关闭按钮）', async () => {
    render(<MemoryLedgers />)

    const drawer = await openScope('memory')
    const dialog = screen.getByRole('dialog')

    // 抬头写的是「l2-course-x 的记忆」，不是「这个 Agent」——界面上没有「这个」
    // 的指代对象；也不再补一句「只有 X 会读这一份」式的说明。
    expect(within(dialog).getByText('l2-course-x 的记忆')).toBeTruthy()
    expect(within(dialog).queryByText(/会读/)).toBeNull()

    // 窗口顶栏那 34px 归系统的最小化/最大化/关闭（Windows 的 WCO 永远压在网页之上）：
    // 抽屉底色照旧铺满全高，内容与关闭按钮必须从它下面开始，否则两者正好重合。
    const content = drawer.closest('[data-slot="sheet-content"]')
    expect(content?.className).toContain('var(--titlebar-height')
  })

  it('跨 Agent 的那两份不写归属（共用纪律与画像本来就不属于某一个 Agent）', async () => {
    render(<MemoryLedgers />)

    const drawer = await openScope('global')
    expect(within(screen.getByRole('dialog')).queryByText(/会读/)).toBeNull()
    expect(within(drawer).getByText('共用甲')).toBeTruthy()
  })

  it('空的那一份点开说的是「暂无条目」，不留白', async () => {
    render(<MemoryLedgers />)

    const drawer = await openScope('user')
    expect(within(drawer).getByText('暂无条目')).toBeTruthy()
  })

  it('删除要两下：第一下只上膛，第二下才真删并用新快照替换', async () => {
    render(<MemoryLedgers />)

    const drawer = await openScope('memory')
    const api = (window as unknown as { plobiDesktop: { api: ReturnType<typeof vi.fn> } }).plobiDesktop.api

    fireEvent.click(within(drawer).getByText('删除'))
    expect(api).not.toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/forget' }))

    fireEvent.click(within(drawer).getByText('确认删除'))

    await waitFor(
      () => expect(api).toHaveBeenCalledWith(expect.objectContaining({ method: 'POST', path: '/api/memory/forget' })),
      { timeout: 3000 }
    )
    await waitFor(() => expect(screen.queryByText('周三有实验')).toBeNull(), { timeout: 3000 })
  })

  it('后端还是旧进程（404）时说的是「重启后可用」，不是笼统的读不到', async () => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async () => {
        throw new Error('404 Not Found')
      })
    }
    render(<MemoryLedgers />)

    expect(await screen.findByText('后端版本低于此页面，重启 Plobi 后生效。')).toBeTruthy()
  })

  it('切到英文/日文时四个名字跟着走，后端那四句中文 label 一个字都不上屏', async () => {
    // L1 那份（agent 是 'l1'）才走到「没有绑定到某个 Agent」那一句。
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async ({ path }: { path: string }) =>
        path === '/api/memory/ledgers' ? { ...snapshot, agent: 'l1' } : boundary
      )
    }

    i18nState.locale = 'en'
    const view = render(<MemoryLedgers />)

    expect(await screen.findByText('Global memory')).toBeTruthy()
    expect(screen.getByText('User profile')).toBeTruthy()
    // l1 那两份挂在指挥名下：Conductor's memory / Conductor's state。
    expect(screen.getByText("Conductor's memory")).toBeTruthy()

    // 后端 `memory_api.py` 写死的中文标签不许当兜底漏出来。
    for (const leaked of ['共用纪律', '你的画像', '这个 Agent 自己的记忆']) {
      expect(view.container.textContent).not.toContain(leaked)
    }

    // 这一行原先写的是「总秘书」——早就不叫这个名字了。
    expect(view.container.textContent).toContain('Conductor')

    i18nState.locale = 'ja'
    const ja = render(<MemoryLedgers />)
    expect(await screen.findByText('グローバル記憶')).toBeTruthy()
    expect(ja.container.textContent).not.toContain('共用纪律')
  })
})

describe('MemoryBoundary', () => {
  it('每类一行：写入连采用 5 的分母，与上面那行 8 分得开', async () => {
    render(<MemoryBoundary />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('连续 3/5')).toBeTruthy()
    expect(within(panel).getByText('连续 2/5')).toBeTruthy()
    expect(within(panel).getByText('驳回 1 次')).toBeTruthy()
    expect(within(panel).getByText('已授权')).toBeTruthy()
    expect(within(panel).getByText('未授权')).toBeTruthy()
  })

  it('已生效政策给原文，并说清怎么收回', async () => {
    render(<MemoryBoundary />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('类别 wiki 的写入：你连着点头 5 次，以后这类我不再问。')).toBeTruthy()
    expect(within(panel).getByText('删除此行后恢复为每次询问')).toBeTruthy()
  })

  it('最近一次静默能拼出「写进哪、哪一类、凭哪条政策」', async () => {
    render(<MemoryBoundary />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('已写入 Mind/wiki/daily/2026-11-17.md（wiki · promote:wiki）')).toBeTruthy()
  })

  it('熔断时最上方出一条告警，写明几次、几天', async () => {
    installApi({ boundary: { ...boundary, breaker: { count: 3, threshold: 3, tripped: true, window_days: 7 } } })
    render(<MemoryBoundary />)

    const panel = await screen.findByTestId('memory-boundary')
    const alert = within(panel).getByRole('alert')
    expect(alert.textContent).toContain('7 天内驳回 3/3 次')
  })

  it('全新安装（没有表态史）时说的是「还没有值得学的」，不留白', async () => {
    installApi({ boundary: { ...boundary, categories: {}, policies: [], traces: [] } })
    render(<MemoryBoundary />)

    const panel = await screen.findByTestId('memory-boundary')
    expect(within(panel).getByText('暂无数据。批准或驳回若干次后显示统计。')).toBeTruthy()
    expect(within(panel).getByText('暂无记录')).toBeTruthy()
  })

  it('老后端没有这个口时整块不渲染，也不冒红字', async () => {
    const api = installApi({ boundaryFails: true })
    const view = render(<MemoryBoundary />)

    // 请求确实发出去、也失败了；失败之后仍然一行都不画（不是画一个「读不到」）。
    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/boundary' })))
    expect(screen.queryByTestId('memory-boundary')).toBeNull()
    expect(view.container.textContent).toBe('')
    expect(screen.queryByText('无法读取记忆')).toBeNull()
  })
})
