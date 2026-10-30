/**
 * 设置里那一页「记忆」：三份按条列出、忘掉两下才真删且带走回执。
 *
 * 现核那条链：`GET /api/memory/ledgers` → 逐条渲染 → 第一下只上膛 → 第二下
 * `POST /api/memory/forget`，页面必须换成服务器回来的新快照（不是本地先删）。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryLedgers } from './ledgers'

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

describe('MemoryLedgers', () => {
  beforeEach(() => {
    const api = vi.fn(async ({ path }: { path: string }) => {
      if (path === '/api/memory/ledgers') return snapshot
      return { ...snapshot, ok: true, removed: '周三有实验', scopes: [{ ...snapshot.scopes[2], chars: 0, entries: [] }] }
    })
    ;(window as unknown as { plobiDesktop: { api: typeof api } }).plobiDesktop = { api }
  })

  it('按条列出三份记忆，并显示已用/上限与信任账', async () => {
    render(<MemoryLedgers />)

    expect(await screen.findByText('共用甲')).toBeTruthy()
    expect(screen.getByText('共用乙')).toBeTruthy()
    expect(screen.getByText('周三有实验')).toBeTruthy()
    expect(screen.getByText('6/2200')).toBeTruthy()
    expect(screen.getByText('连采 3/8')).toBeTruthy()
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
