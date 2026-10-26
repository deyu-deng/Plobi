// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useStaleAfter } from './use-stale-after'

/**
 * 上限之后要停：一个 30 秒才超时的请求不该让界面闪 30 秒。
 */
describe('useStaleAfter', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('刚开始不 stale，过了上限就 stale', () => {
    const { result } = renderHook(() => useStaleAfter(true, 8000))

    expect(result.current).toBe(false)
    act(() => void vi.advanceTimersByTime(7999))
    expect(result.current).toBe(false)
    act(() => void vi.advanceTimersByTime(1))
    expect(result.current).toBe(true)
  })

  it('加载态一结束就复位，下一次重新计时', () => {
    const { result, rerender } = renderHook(({ active }) => useStaleAfter(active, 8000), {
      initialProps: { active: true }
    })

    act(() => void vi.advanceTimersByTime(8000))
    expect(result.current).toBe(true)

    rerender({ active: false })
    expect(result.current).toBe(false)

    rerender({ active: true })
    expect(result.current).toBe(false)
  })

  it('不活跃时永远不 stale', () => {
    const { result } = renderHook(() => useStaleAfter(false, 100))
    act(() => void vi.advanceTimersByTime(60_000))
    expect(result.current).toBe(false)
  })
})
