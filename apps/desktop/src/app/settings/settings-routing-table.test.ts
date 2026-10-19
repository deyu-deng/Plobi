import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * A nav item that is missing from `SETTINGS_VIEWS` does not error and does not
 * log — `useRouteEnumParam` silently folds an unknown id back to the fallback
 * view, so the pane renders the list row, the click "works", and nothing opens.
 * That is how the Network pane shipped broken on 2026-10-12, so the two lists
 * are checked against each other here instead of trusting a hand-edited array.
 */
const source = readFileSync(resolve(import.meta.dirname ?? '', 'index.tsx'), 'utf-8')

const whitelisted = new Set(
  // The last entry has no trailing comma, so `,?` — otherwise `about` reads as
  // unroutable and this file cries about a pane that works fine.
  [...source.matchAll(/^\s{2}'([a-z-]+)',?$/gm)].map((m) => m[1])
)

const navIds = [...source.matchAll(/\bid: '([a-z-]+)',$/gm)]
  .map((m) => m[1])
  // `config:*` ids are derived from SECTIONS, not listed as literals here.
  .filter((id) => !['config', 'kview', 'pview'].includes(id))
  // `capabilities` is a link-out to the global source page (/skills), not a
  // settings pane — 2026-10-19 裁定：设置里只放总入口，不复制面板。
  .filter((id) => id !== 'capabilities')

describe('settings routing table', () => {
  it('lists every nav pane in the route whitelist', () => {
    expect(navIds.length).toBeGreaterThan(3)
    for (const id of navIds) {
      expect(whitelisted, `${id} is clickable but not routable`).toContain(id)
    }
  })

  it('keeps the egress pane routable', () => {
    expect(navIds).toContain('network')
    expect(whitelisted).toContain('network')
  })

  /**
   * The command palette is the second door into the same panes, so its `tab`
   * values are gated by the very same whitelist — an entry there that is missing
   * here fails the same invisible way a nav row does.
   */
  it('makes every palette shortcut a routable settings tab', () => {
    const palette = readFileSync(resolve(import.meta.dirname ?? '', '../command-palette/index.tsx'), 'utf-8')
    const tabs = [...palette.matchAll(/labelKey: '([a-zA-Z]+)',\s*tab: '([^']+)'/g)].map((m) => m[2])

    expect(tabs.length).toBeGreaterThan(4)
    for (const tab of tabs) {
      // `providers&pview=keys` style values are routed by their leading segment;
      // the sub-view param has its own enum.
      const head = tab.split('&')[0]
      if (head.startsWith('config:')) continue
      expect(whitelisted, `palette tab "${tab}" is not a routable settings view`).toContain(head)
    }
    expect(tabs).toContain('network')
  })
})
