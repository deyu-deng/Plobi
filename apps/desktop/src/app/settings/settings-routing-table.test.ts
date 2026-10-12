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
})
