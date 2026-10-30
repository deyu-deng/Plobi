/**
 * 角标那颗 store：过期纸不该在屏上冒充"等你点头"——读到就顺手清（后端先归档再删）。
 * 清失败不影响显示（过期那条仍带 expired 标）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $mindPending, refreshMindPending } from './mind-pending'

const listing = (expired: number, total: number) => ({
  count: total,
  expired_count: expired,
  gate: true,
  auto: false,
  items: Array.from({ length: total }, (_, i) => ({
    expired: i < expired,
    id: `p${i}`,
    kind: 'memory',
    paths: ['wiki/daily/x.md'],
    summary: 'x'
  })),
  scopes: []
})

describe('refreshMindPending', () => {
  beforeEach(() => {
    ;(window as unknown as { plobiDesktop: { api: unknown } }).plobiDesktop = {
      api: vi.fn(async ({ path }: { path: string }) => (path.endsWith('/prune') ? listing(0, 1) : listing(2, 3)))
    }
  })

  it('有过期纸时先清一次，并把清完的结果放回 store', async () => {
    const api = (window as unknown as { plobiDesktop: { api: ReturnType<typeof vi.fn> } }).plobiDesktop.api

    const state = await refreshMindPending()

    expect(api).toHaveBeenCalledWith(expect.objectContaining({ method: 'POST', path: '/api/memory/pending/prune' }))
    expect(state.expired_count).toBe(0)
    expect($mindPending.get().count).toBe(1)
  })
})
