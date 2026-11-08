import { describe, expect, it } from 'vitest'

import { SASH_GLOW_CENTER_VAR, sashGlowCenter, sashGlowStyle } from './sash'

describe('sash glow', () => {
  it('paints a transparent-ended gradient, not a solid band', () => {
    const style = sashGlowStyle('x')
    const image = style.backgroundImage ?? ''

    expect(image).toContain('linear-gradient(to bottom')
    expect(image).toContain('transparent 0%')
    expect(image).toContain('transparent 100%')
    // The core of the glow is the brand color, the shoulders fade into it.
    expect(image).toContain('var(--ui-accent)')
    expect(image).toContain('var(--ui-sash-hover-border)')
  })

  it('runs the gradient along the sash axis', () => {
    expect(sashGlowStyle('y').backgroundImage).toContain('linear-gradient(to right')
  })

  it('reads the center from the sash-length variable', () => {
    expect(sashGlowStyle('x').backgroundImage).toContain(`var(${SASH_GLOW_CENTER_VAR}, 50%)`)
  })
})

describe('sashGlowCenter', () => {
  const pointerAt = (clientX: number, clientY: number, rect: Partial<DOMRect>) => ({
    clientX,
    clientY,
    currentTarget: {
      getBoundingClientRect: () => ({
        bottom: 0,
        height: 0,
        left: 0,
        right: 0,
        top: 0,
        width: 0,
        x: 0,
        y: 0,
        ...rect
      })
    }
  })

  it('tracks the pointer along a vertical sash', () => {
    const event = pointerAt(0, 140, { top: 20, height: 400 })

    expect(sashGlowCenter(event as never, 'x')).toBe(120)
  })

  it('tracks the pointer along a horizontal sash', () => {
    const event = pointerAt(310, 0, { left: 100, width: 500 })

    expect(sashGlowCenter(event as never, 'y')).toBe(210)
  })

  it('clamps the peak to the sash', () => {
    const above = pointerAt(0, -50, { top: 20, height: 400 })
    const below = pointerAt(0, 9999, { top: 20, height: 400 })

    expect(sashGlowCenter(above as never, 'x')).toBe(0)
    expect(sashGlowCenter(below as never, 'x')).toBe(400)
  })
})
