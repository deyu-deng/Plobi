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

  it('官方标志抓得到的那几家，上屏的就是它自己的标志', () => {
    // 用户 11-03：「感觉你用的是伪图标，直接用真实的图标」。这三家都从各家官网取。
    // 小文件会被 Vite 内联成 data URI，所以判据是"是一枚 SVG、且三家各不相同"，
    // 不是文件名——写文件名等于把打包方式当契约。
    const sources: string[] = []

    for (const id of ['kiro_cli', 'qoder', 'antigravity']) {
      const { container } = render(<QuotaAppIcon id={id} name={id} />)
      const img = container.querySelector('img')

      expect(img, `${id} 没有渲染官方标志`).not.toBeNull()
      expect(img?.getAttribute('alt')).toBe('')
      expect(decodeURIComponent(img?.getAttribute('src') ?? '')).toMatch(/^data:image\/svg\+xml|^.*\.svg$/)
      sources.push(img?.getAttribute('src') ?? '')
    }

    expect(new Set(sources).size, '三家渲染了同一枚标志').toBe(3)
  })

  it('确实拿不到官方标志的，才退回品牌色底 + 首字母', () => {
    // Codex 停在这一档：openai.com 与 developers.openai.com 在这台都回 403。
    // 宁可首字母，也不画一个假 logo 替它认领身份。
    const { container } = render(<QuotaAppIcon id="codex_cli" name="Codex" />)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toBeNull()
    expect(screen.getByText('C')).toBeTruthy()
  })

  it('stays out of the accessibility tree either way', () => {
    const { container } = render(<QuotaAppIcon id="cursor" name="Cursor" />)

    expect(container.querySelector('[aria-hidden]')).toBeTruthy()
    expect(screen.queryByText('Cursor')).toBeNull()
  })

  it('magpie 迁移后换了腿 id 的那几家，品牌字形还在', () => {
    // 2026-11-09 全量迁移：额度栏那张卡的腿 id ＝ magpie 的 provider id
    // （`cline_cli` → `cline`、`kiro_cli` → `kiro`、`antigravity_cli` → `agy-cli`、
    // `opencode_free` → `opencode-zen-free`）。图标表是**按腿 id** 查的，键不跟过去
    // ⇒ 品牌字形当场消失，卡片退回一个首字母（用户 11-10：「图标怎么没有加」）。
    const ids = [
      'cline',
      'kiro',
      'agy-cli',
      'opencode-zen-free',
      'opencode_api',
      'github-copilot',
      'qoder',
      'qoder-cn',
      'qwenwork',
      'qwenwork-enterprise',
      'trae-cn',
      'trae-global',
      'mimo-app',
      'minimax-code',
      'minimax-code-global',
      'cursor',
      'workbuddy',
      'workbuddy-ai',
      'devin',
      'zcode'
    ]

    for (const id of ids) {
      const { container } = render(<QuotaAppIcon id={id} name={id} />)

      // 品牌字形（simple-icons 那一族）或官方标志文件——两者都算「有图标」。
      const hasGlyph = container.querySelector('svg') !== null
      const hasMark = container.querySelector('img') !== null

      expect(hasGlyph || hasMark, `${id} 没有品牌字形`).toBe(true)
    }
  })

  it('拿不到官方标志的那几家，退回首字母——不画一个假 logo 替它认领身份', () => {
    // Grok／JoyCode／Tabbit：simple-icons 没有，官网也只给 .ico 或抓不到。
    // 与 Codex 那一枚同一个理由（用户 11-03：「感觉你用的是伪图标」）。
    for (const id of ['grok', 'joycode', 'tabbit']) {
      const { container } = render(<QuotaAppIcon id={id} name={id} />)

      expect(container.querySelector('img'), `${id} 不该有标志文件`).toBeNull()
      expect(container.querySelector('svg'), `${id} 不该有字形`).toBeNull()
      expect(screen.getByText(id.charAt(0).toUpperCase()), id).toBeTruthy()
    }
  })
})
