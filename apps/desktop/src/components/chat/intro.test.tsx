import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Intro, introGreetingCandidates, resolveIntroCopy } from './intro'

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

/**
 * The copy pool behind the empty-state greeting (WP-BRAND-THEME-FOLLOW · 第三段).
 *
 * The pool is `intro-copy.jsonl` — one JSON record per line. A record may carry
 * a `locale`; the ones written before locales existed are English. The console
 * empty state shows one of these lines, so the pool has to answer "what does
 * this screen say" in the language the user is actually running.
 */
describe('Intro copy pool', () => {
  it('turns a different greeting for a different seed', () => {
    const first = resolveIntroCopy({ locale: 'en', seed: 0 }).headline
    const second = resolveIntroCopy({ locale: 'en', seed: 1 }).headline

    expect(first).not.toBe(second)
  })

  it('answers in the current language', () => {
    const candidates = introGreetingCandidates('zh')

    expect(candidates.length).toBe(5)
    expect(
      candidates.every(line => /[\u4e00-\u9fff]/.test(line)),
      '中文那档必须是中文'
    ).toBe(true)
  })

  it('falls back to the English pool for a language without its own set', () => {
    expect(introGreetingCandidates('fr')).toEqual(introGreetingCandidates('en'))
  })

  it('keeps every line short enough for one line on screen', () => {
    for (const locale of ['en', 'zh', 'zh-hant', 'ja']) {
      for (const line of introGreetingCandidates(locale)) {
        // 屏上只留一句招呼：中日文按 16 字、拉丁文按 40 字符。
        const limit = /[\u4e00-\u9fff]/.test(line) ? 16 : 40

        expect(line.length, `${locale}：${line}`).toBeLessThanOrEqual(limit)
      }
    }
  })
})
