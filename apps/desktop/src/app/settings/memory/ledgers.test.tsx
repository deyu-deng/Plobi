/**
 * 设置里那一页「记忆」：三份按条列出、忘掉只删一条且带走回执。
 *
 * 现核那条链：`GET /api/memory/ledgers` → 逐条渲染 → `POST /api/memory/forget` 之后
 * 页面必须换成服务器回来的新快照（而不是本地先删、等下次刷新又冒出来）。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryLedgers } from './ledgers'

const copy = {
  agentLine: (agent: string) => `这份 Agent：${agent}`,
  empty: '还没记住什么',
  failed: '没删成',
  forget: '忘掉',
  forgetHint: '删掉这一条，并留一行回执',
}

vi.mock('@/i18n', () => ({
  useI18n: () => ({ locale: 'zh', t: {} })
}))

const snapshot = {
  agent: 'l2-course-x',
  scopes: [
    { chars: 6, entries: [{ index: 0, text: '共用甲' }, { index: 1, text: '共用乙' }], key: 'global', label: '共用纪律', limit: 2200, present: true },
    { chars: 0, entries: [], key: 'user', label: '你的画像', limit: 1375, present: false },
    { chars: 8, entries: [{ index: 0, text: '周三有实验' }], key: 'memory', label: '这个 Agent 自己的记忆', limit: 2200, present: true, streak: 3, threshold: 8 }
  ]
}

describe('MemoryLedgers', () => {
  beforeEach(() => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/memory/ledgers') return snapshot
      return { ...snapshot, ok: true, removed: '周三有实验', scopes: [{ ...snapshot.scopes[2], chars: 0, entries: [] }] }
    })
    ;(window as unknown as { plobiDesktop: { api: typeof api } }).plobiDesktop = { api }
  })

  it('按条列出三份记忆，并显示已用字符与上限', async () => {
    render(<MemoryLedgers />)

    expect(await screen.findByText('共用甲')).toBeTruthy()
    expect(screen.getByText('共用乙')).toBeTruthy()
    expect(screen.getByText('周三有实验')).toBeTruthy()
    expect(screen.getByText('6/2200')).toBeTruthy()
    // 信任账那一格：屏幕上要能解释"为什么这条还在问我"（连采 3/8）
    expect(screen.getByText('连采 3/8')).toBeTruthy()
    expect(screen.getAllByText('还没记住什么').length).toBe(1) // 只有画像那份是空的
  })

  it('忘掉一条之后，页面用服务器回来的新快照替换（不是本地先删）', async () => {
    // 档间不自动 cleanup（见 plobi-desktop-tsc-emitted-js-shadow 那条）：断言"不存在"必须 within 当前容器
    const view = render(<MemoryLedgers />)
    const box = within(view.container)
    await box.findByText('周三有实验')

    const api = (window as unknown as { plobiDesktop: { api: ReturnType<typeof vi.fn> } }).plobiDesktop.api
    fireEvent.click(box.getAllByText('忘掉')[2])

    await waitFor(() => expect(api).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/memory/forget' })), { timeout: 3000 })
    await waitFor(() => expect(box.queryByText('周三有实验')).toBeNull(), { timeout: 3000 })
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ method: 'POST', path: '/api/memory/forget' }))
  })
})
