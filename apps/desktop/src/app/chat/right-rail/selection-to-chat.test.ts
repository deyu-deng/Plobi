import { describe, expect, it } from 'vitest'

import { anchorForSelection } from './selection-to-chat'

function fakeHost(clientWidth: number, clientHeight: number, originTop = 0, originLeft = 0) {
  return {
    clientWidth,
    clientHeight,
    getBoundingClientRect: () => ({ left: originLeft, top: originTop })
  } as unknown as HTMLDivElement
}

describe('anchorForSelection', () => {
  it('sits just below the selection', () => {
    const anchor = anchorForSelection(fakeHost(400, 600), { left: 40, bottom: 120 })

    expect(anchor).toEqual({ left: 40, top: 124 })
  })

  it('never escapes the right edge of the rail', () => {
    const anchor = anchorForSelection(fakeHost(200, 600), { left: 1_000, bottom: 60 })

    expect(anchor.left).toBe(200 - 132)
  })

  it('never escapes the bottom edge, even on a narrow rail', () => {
    const anchor = anchorForSelection(fakeHost(120, 90), { left: 0, bottom: 500 })

    expect(anchor.left).toBeGreaterThanOrEqual(8)
    expect(anchor.top).toBe(90 - 34)
  })

  it('offsets from the host origin, not the viewport', () => {
    const anchor = anchorForSelection(fakeHost(400, 600, 200, 100), { left: 140, bottom: 260 })

    expect(anchor).toEqual({ left: 40, top: 64 })
  })
})
