import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { officeViewStateKey } from './office-view-state'
import { useOfficeScrollMemory } from './use-office-scroll-memory'

/**
 * jsdom 不做排版：scrollHeight/clientHeight 恒为 0，必须自己编一份，否则任何
 * 恢复逻辑都会因为「没有可滚动的余量」而提前退出。
 */
function makeHost({ room = 800, viewport = 200 } = {}): HTMLDivElement {
  const host = document.createElement('div')

  Object.defineProperty(host, 'scrollHeight', { configurable: true, value: room + viewport })
  Object.defineProperty(host, 'clientHeight', { configurable: true, value: viewport })

  return host
}

const key = officeViewStateKey('pdf', '/a/spec.pdf')

beforeEach(() => {
  window.localStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  window.localStorage.clear()
})

function mount(spec = {}) {
  const hook = renderHook(() => useOfficeScrollMemory({ kind: 'pdf', path: '/a/spec.pdf' }))
  const host = makeHost(spec)

  act(() => hook.result.current(host))

  return { ...hook, host }
}

describe('useOfficeScrollMemory', () => {
  it('restores a saved position by ratio, not by pixels', () => {
    window.localStorage.setItem(key, JSON.stringify({ at: 1, ratio: 0.5 }))

    const { host } = mount()

    act(() => vi.advanceTimersByTime(600))

    expect(host.scrollTop).toBe(400)
  })

  it('starts at the top when nothing was saved', () => {
    const { host } = mount()

    act(() => vi.advanceTimersByTime(600))

    expect(host.scrollTop).toBe(0)
    expect(window.localStorage.getItem(key)).toBeNull()
  })

  it('keeps waiting while the document is still growing (pdf draws page by page)', () => {
    window.localStorage.setItem(key, JSON.stringify({ at: 1, ratio: 0.5 }))

    const host = makeHost({ room: 0, viewport: 200 })
    const hook = renderHook(() => useOfficeScrollMemory({ kind: 'pdf', path: '/a/spec.pdf' }))

    act(() => hook.result.current(host))
    act(() => vi.advanceTimersByTime(600))
    expect(host.scrollTop).toBe(0)

    Object.defineProperty(host, 'scrollHeight', { configurable: true, value: 1000 })
    act(() => vi.advanceTimersByTime(600))
    expect(host.scrollTop).toBe(400)
  })

  it('hands control back the moment the user scrolls or clicks', () => {
    window.localStorage.setItem(key, JSON.stringify({ at: 1, ratio: 0.9 }))

    const { host } = mount()

    act(() => {
      host.dispatchEvent(new Event('wheel'))
      host.scrollTop = 100
      vi.advanceTimersByTime(600)
    })

    expect(host.scrollTop).toBe(100)
  })

  it('saves a throttled position while scrolling', () => {
    const { host } = mount()

    act(() => {
      host.scrollTop = 200
      host.dispatchEvent(new Event('scroll'))
      vi.advanceTimersByTime(500)
    })

    expect(JSON.parse(String(window.localStorage.getItem(key))).ratio).toBeCloseTo(0.25)
  })

  it('saves on unmount so closing the tab still remembers', () => {
    const { host, unmount } = mount()

    act(() => {
      host.scrollTop = 600
      unmount()
    })

    expect(JSON.parse(String(window.localStorage.getItem(key))).ratio).toBeCloseTo(0.75)
  })

  it('does not overwrite a position just because the pane is too short to scroll', () => {
    window.localStorage.setItem(key, JSON.stringify({ at: 1, ratio: 0.5 }))

    const { host, unmount } = mount({ room: 0, viewport: 200 })

    act(() => unmount())

    expect(JSON.parse(String(window.localStorage.getItem(key))).ratio).toBe(0.5)
    expect(host.scrollTop).toBe(0)
  })

  it('ignores a corrupt snapshot instead of scrolling to garbage', () => {
    window.localStorage.setItem(key, 'null')

    const { host } = mount()

    act(() => vi.advanceTimersByTime(600))

    expect(host.scrollTop).toBe(0)
  })

  it('stops touching the node once the file changes to another path', () => {
    const hook = renderHook(({ path }: { path: string }) => useOfficeScrollMemory({ kind: 'pdf', path }), {
      initialProps: { path: '/a/one.pdf' }
    })

    const host = makeHost()

    act(() => hook.result.current(host))
    act(() => vi.advanceTimersByTime(200))

    window.localStorage.setItem(officeViewStateKey('pdf', '/a/two.pdf'), JSON.stringify({ at: 1, ratio: 1 }))
    act(() => {
      hook.rerender({ path: '/a/two.pdf' })
    })
    act(() => vi.advanceTimersByTime(600))

    expect(host.scrollTop).toBe(800)
  })
})
