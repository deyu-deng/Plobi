import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { DetailPane } from './master-detail'

function renderDetailPane() {
  const rendered = render(
    <I18nProvider configClient={null}>
      <DetailPane id="detail" title="Detail">
        body
      </DetailPane>
    </I18nProvider>
  )

  return rendered
}

describe('DetailPane sash', () => {
  afterEach(() => {
    cleanup()
  })

  it('keeps the sash a 1px line — hovering never grows a wide band', () => {
    const rendered = renderDetailPane()
    const sash = rendered.container.querySelector('[class*="group/sash"]')

    expect(sash).not.toBeNull()

    // The old shape: a filled strip as wide as the hover size (4px).
    expect(sash?.querySelector('[class*="sash-hover-size"]')).toBeNull()
    expect(sash?.querySelector('[class*="bg-(--ui-sash-hover-border)"]')).toBeNull()

    // The new shape: a gradient glow painted on the 1px line itself.
    const glow = sash?.querySelector('[style*="linear-gradient"]')

    expect(glow).not.toBeNull()
    expect(glow?.className).toContain('h-px')
    expect(glow?.className).toContain('group-hover/sash:opacity-100')
  })

  it('runs the glow gradient along the horizontal sash', () => {
    const rendered = renderDetailPane()
    const glow = rendered.container.querySelector('[style*="linear-gradient"]')

    expect(glow?.getAttribute('style')).toContain('to right')
  })
})
