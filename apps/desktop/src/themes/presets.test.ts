import { describe, expect, it } from 'vitest'

import { BUILTIN_THEME_LIST, BUILTIN_THEMES, DEFAULT_TYPOGRAPHY, EMOJI_FALLBACK } from './presets'

const luminance = (hex: string) => {
  const channels = [0, 2, 4].map(i => parseInt(hex.slice(1 + i, 3 + i), 16) / 255)
  const linear = channels.map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))

  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)

  return (hi + 0.05) / (lo + 0.05)
}

// #40364: none of the UI text/mono fonts carry emoji glyphs, so every font
// stack must end with a color-emoji fallback or emoji render as tofu on
// platforms whose default font lacks them (e.g. Linux).
describe('theme typography emoji fallback (#40364)', () => {
  const stacks: Array<[string, string]> = [
    ['DEFAULT_TYPOGRAPHY.fontSans', DEFAULT_TYPOGRAPHY.fontSans],
    ['DEFAULT_TYPOGRAPHY.fontMono', DEFAULT_TYPOGRAPHY.fontMono],
    // A theme may override only fontMono (fontSans then falls back to the
    // default, which already carries the emoji stack), so skip undefined.
    ...BUILTIN_THEME_LIST.flatMap(theme =>
      (
        [
          [`${theme.name}.fontSans`, theme.typography?.fontSans],
          [`${theme.name}.fontMono`, theme.typography?.fontMono]
        ] as Array<[string, string | undefined]>
      ).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    )
  ]

  it.each(stacks)('%s includes a color-emoji font', (_label, stack) => {
    expect(stack).toMatch(/Apple Color Emoji|Segoe UI Emoji|Noto Color Emoji|(^|,\s*)emoji\b/)
  })

  it('EMOJI_FALLBACK lists the major platform emoji fonts', () => {
    expect(EMOJI_FALLBACK).toContain('Apple Color Emoji')
    expect(EMOJI_FALLBACK).toContain('Segoe UI Emoji')
    expect(EMOJI_FALLBACK).toContain('Noto Color Emoji')
  })
})

/**
 * The Plobi skin (WP-BRAND-THEME-FOLLOW · 裁定 213).
 *
 * The brand color is not a second color system bolted onto the app — it is one
 * more skin in this table. Everything that follows the theme (the wordmark,
 * the brand glyph, focus rings) follows whichever skin is active.
 */
describe('Plobi skin', () => {
  const theme = BUILTIN_THEMES.plobi

  it('is registered as a switchable skin', () => {
    expect(theme).toBeDefined()
    expect(theme.label).toBe('Plobi')
    expect(BUILTIN_THEME_LIST.map(t => t.name)).toContain('plobi')
  })

  it('uses the measured brand terracotta as its primary, light and dark', () => {
    expect(theme.colors.primary).toBe('#CD6B43')
    expect(theme.darkColors?.primary).toBe('#CD6B43')
  })

  it('has a readable foreground on that primary (AA body text)', () => {
    // White on terracotta is only 3.63:1 — the brand ink #241812 is 4.76:1.
    expect(contrast(theme.colors.primary, theme.colors.primaryForeground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(theme.darkColors!.primary, theme.darkColors!.primaryForeground)).toBeGreaterThanOrEqual(4.5)
  })

  it('keeps body text readable on its own surfaces', () => {
    expect(contrast(theme.colors.background, theme.colors.foreground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(theme.darkColors!.background, theme.darkColors!.foreground)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(theme.colors.background, theme.colors.mutedForeground)).toBeGreaterThanOrEqual(4.5)
  })

  it('the terracotta mark still clears 3:1 against its own backgrounds', () => {
    expect(contrast(theme.colors.primary, theme.colors.background)).toBeGreaterThanOrEqual(3)
    expect(contrast(theme.darkColors!.primary, theme.darkColors!.background)).toBeGreaterThanOrEqual(3)
  })
})
