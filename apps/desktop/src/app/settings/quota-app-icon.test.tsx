import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { QuotaAppIcon } from './quota-app-icon'

/**
 * The first slot of the card grammar (`Docs/specs/settings-card-system.md` §3): brand
 * glyph when one is published, brand-coloured monogram when it is not — and the whole
 * thing is decoration, so it must not put a second copy of the software's name on the
 * screen or in the accessibility tree.
 */
describe('QuotaAppIcon', () => {
  it('draws the brand glyph for a leg that has one', () => {
    const { container } = render(<QuotaAppIcon id="workbuddy" name="CodeBuddy" />)

    expect(container.querySelector('svg')).toBeTruthy()
    // simple-icons ships an unconditional <title> whose default is the brand name.
    expect(container.querySelector('title')?.textContent ?? '').toBe('')
  })

  it('falls back to the initial for a brand with no published glyph', () => {
    // Antigravity and Kiro are in that group on purpose: drawing a third-party logo for
    // them would be Plobi claiming an identity the brand owner withheld.
    const { container } = render(<QuotaAppIcon id="kiro_cli" name="Kiro" />)

    expect(container.querySelector('svg')).toBeNull()
    expect(screen.getByText('K')).toBeTruthy()
  })

  it('stays out of the accessibility tree either way', () => {
    const { container } = render(<QuotaAppIcon id="cursor" name="Cursor" />)

    expect(container.querySelector('[aria-hidden]')).toBeTruthy()
    expect(screen.queryByText('Cursor')).toBeNull()
  })
})
