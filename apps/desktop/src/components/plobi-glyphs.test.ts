import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { PLOBI_MARK_PATH, PLOBI_WORDMARK } from './plobi-glyphs'

const REPO_ROOT = join(process.cwd(), '..', '..')

/**
 * The generated glyph table must be the **same drawing** as the brand masters
 * under `Code/brand/` — one primitive table, two writings. That is the true
 * source discipline (裁定 72) landing on brand assets: if someone edits either
 * side by hand the two drift apart and the UI stops being the brand.
 */
describe('brand glyph table', () => {
  it('is the same turtle as brand/plobi-mark-mono.svg', () => {
    const svg = readFileSync(join(REPO_ROOT, 'brand/plobi-mark-mono.svg'), 'utf8')

    expect(svg).toContain(PLOBI_MARK_PATH)
  })

  it('is the same wordmark as brand/plobi-wordmark-mono.svg', () => {
    const svg = readFileSync(join(REPO_ROOT, 'brand/plobi-wordmark-mono.svg'), 'utf8')

    expect(svg).toContain(PLOBI_WORDMARK.tittle.path)

    for (const shape of PLOBI_WORDMARK.shapes) {
      if (shape.kind === 'line') {
        expect(svg).toContain(`x1="${shape.x1}" y1="${shape.y1}" x2="${shape.x2}" y2="${shape.y2}"`)
      } else {
        expect(svg).toContain(`cx="${shape.cx}" cy="${shape.cy}"`)
      }
    }
  })

  it('draws the four letter stems and the three bowls', () => {
    const lines = PLOBI_WORDMARK.shapes.filter(shape => shape.kind === 'line')
    const bowls = PLOBI_WORDMARK.shapes.filter(shape => shape.kind === 'circle')

    expect(lines).toHaveLength(4)
    expect(bowls).toHaveLength(3)
  })

  it('the wordmark carries no baked color — it must follow the theme', () => {
    const svg = readFileSync(join(REPO_ROOT, 'brand/plobi-wordmark-mono.svg'), 'utf8')

    expect(svg).toContain('currentColor')
    expect(svg).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })
})
