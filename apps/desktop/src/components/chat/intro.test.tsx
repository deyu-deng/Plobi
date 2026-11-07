import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Intro } from './intro'

/**
 * The chat empty state's wordmark is the **brand geometric wordmark**, drawn
 * inline so it can take the theme color (WP-BRAND-THEME-FOLLOW · 裁定 213).
 *
 * It used to be live serif text wearing a `fit-text` + gradient trick: two
 * design languages at once, on the most expensive slot in the product. The
 * "i" of the geometric wordmark is dotted by the brand turtle, so the hero
 * mark is the product's own glyph rather than a decorative dot.
 */
describe('Intro wordmark', () => {
  it('draws the brand wordmark as inline SVG instead of live text', () => {
    const { container } = render(<Intro personality="default" seed={1} />)
    const svg = container.querySelector('svg[role="img"]')

    expect(svg).not.toBeNull()
    expect(svg?.getAttribute('aria-label')).toBe('Plobi')
    expect(container.querySelector('p[aria-label="Plobi"]'), '还是活文字那一版').toBeNull()
  })

  it('takes the theme color (currentColor), so switching skins repaints it', () => {
    const { container } = render(<Intro personality="default" seed={1} />)
    const group = container.querySelector('svg[role="img"] > g')
    const strokes = container.querySelectorAll('svg[role="img"] line, svg[role="img"] circle')

    expect(strokes).toHaveLength(7)
    expect(group?.getAttribute('stroke')).toBe('currentColor')
    expect(group?.getAttribute('stroke-width')).toBe('10')
  })

  it('the turtle that dots the "i" is part of the wordmark', () => {
    const { container } = render(<Intro personality="default" seed={1} />)
    const paths = container.querySelectorAll('svg[role="img"] path')

    expect(paths).toHaveLength(1)
    expect(paths[0].getAttribute('fill')).toBe('currentColor')
  })
})
