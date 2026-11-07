import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BrandMark } from './brand-mark'

/**
 * The brand glyph (WP-BRAND-THEME-FOLLOW · 裁定 213).
 *
 * Two things are contractual: it is drawn **inline**, so `currentColor` can
 * reach it — a baked PNG cannot follow the skin the user picks — and its color
 * comes from the **theme**, never from a literal. The old white tile was
 * sanctioned for the old avatar glyph and read as a white brick in dark mode.
 */
describe('BrandMark', () => {
  it('draws the turtle inline so the theme color can reach it', () => {
    const { container } = render(<BrandMark />)
    const svg = container.querySelector('svg')

    expect(svg).not.toBeNull()
    expect(svg?.getAttribute('viewBox')).toBe('0 0 64 64')
    expect(container.querySelector('img'), '还在吃烘死颜色的位图 —— 那样跟不了主题').toBeNull()
  })

  it('takes its color from the theme, never from a literal', () => {
    const { container } = render(<BrandMark />)
    const tile = container.firstElementChild as HTMLElement

    expect(tile.className).toContain('text-(--theme-primary)')
    expect(tile.className).not.toContain('bg-white')
  })

  it('stays out of the accessibility tree (it is decoration)', () => {
    const { container } = render(<BrandMark />)

    expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy()
  })
})
