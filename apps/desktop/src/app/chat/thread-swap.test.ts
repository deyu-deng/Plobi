import { describe, expect, it } from 'vitest'

import { prefersReducedMotion, THREAD_SWAP_DURATION_MS, threadSwapAnimation } from './thread-swap'

describe('threadSwapAnimation', () => {
  it('gives the new content one short fade-and-rise', () => {
    const animation = threadSwapAnimation(false)

    expect(animation).not.toBeNull()
    expect(animation?.options.duration).toBe(THREAD_SWAP_DURATION_MS)
    // 起点是「淡且低一点」，终点是「完全不透明且归位」——切换读起来是一次
    // 过渡，不是一次硬跳。
    expect(animation?.keyframes[0].opacity).toBe(0)
    expect(animation?.keyframes[1].opacity).toBe(1)
    expect(String(animation?.keyframes[0].transform)).toContain('translateY')
    expect(String(animation?.keyframes[1].transform)).toContain('translateY(0)')
  })

  it('does nothing when the system asks for reduced motion', () => {
    // 「系统说了别动，就别动」——不是改成更短的动效。
    expect(threadSwapAnimation(true)).toBeNull()
  })
})

describe('prefersReducedMotion', () => {
  it('reads the system setting', () => {
    const original = window.matchMedia

    try {
      window.matchMedia = ((query: string) => ({
        matches: query.includes('reduce'),
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false
      })) as unknown as typeof window.matchMedia

      expect(prefersReducedMotion()).toBe(true)
    } finally {
      window.matchMedia = original
    }
  })
})
