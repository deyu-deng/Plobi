// @vitest-environment jsdom
/**
 * 厂商图标那一格的判据（用户 11-01：「不要是一个方块加一些文字，而是矢量图标加名词」）。
 *
 * 钉三件事：**不再出现字母方块**（10-22 那版的首字母徽标被点名换掉）、**每家都有一枚
 * 矢量字形**（没登记品牌的一家也不许空白）、**不臆造品牌色**（以"不带内联颜色"为断言）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { OffloadVendorIcon, vendorLabelFor } from './offload-vendor-icon'

afterEach(cleanup)

describe('offload vendor icon', () => {
  it('有品牌字形的那家画字形', () => {
    render(<OffloadVendorIcon vendor="codebuddy" />)

    expect(screen.getByTestId('offload-vendor-glyph-codebuddy')).toBeTruthy()
  })

  it('没有品牌字形的那家画中性矢量图标，不再印首字母', () => {
    render(<OffloadVendorIcon vendor="qoder" />)

    const icon = screen.getByTestId('offload-vendor-fallback-qoder')

    // 一个字母方块会是纯文本；这里必须是字形元素（codicon）。
    expect(icon.querySelector('.codicon')).toBeTruthy()
    expect(icon.textContent).toBe('')
  })

  it('表里没登记的一家也照样有字形', () => {
    render(<OffloadVendorIcon vendor="somefuturetool" />)

    expect(screen.getByTestId('offload-vendor-fallback-somefuturetool').querySelector('.codicon')).toBeTruthy()
  })

  it('不带臆造的品牌色', () => {
    render(<OffloadVendorIcon vendor="qoder" />)

    // 颜色只走主题令牌（class）。写死一个十六进制色＝替那个品牌认领一个它自己没用的身份。
    expect(screen.getByTestId('offload-vendor-fallback-qoder').getAttribute('style')).toBe(null)
  })

  // 用户 10-30：「agy 能不能用别人的官方首字母大写名称，不要用命令行简写」。
  it('屏幕上写品牌名，不写命令行简写', () => {
    expect(vendorLabelFor('agy')).toBe('Antigravity')
    expect(vendorLabelFor('qwen')).toBe('Qwen Code')
    expect(vendorLabelFor('kiro')).toBe('Kiro')
    expect(vendorLabelFor('gemini')).toBe('Gemini CLI')
    expect(vendorLabelFor('claude')).toBe('Claude Code')
    // 11-08 新接的两家：命令行程序名分别是 `copilot` 与 `kilo`，屏上写品牌名。
    expect(vendorLabelFor('copilot')).toBe('GitHub Copilot CLI')
    expect(vendorLabelFor('kilo')).toBe('Kilo Code')
    // 11-15：Qoder 中国版是**另一件安装**（`product: qodercn`、另一个账号）⇒ 单列一家，
    // 屏上写它自己的名字，不与 Qoder 混成一家。
    expect(vendorLabelFor('qoder-cn')).toBe('Qoder 中国版')
    expect(vendorLabelFor('qoder')).toBe('Qoder')
  })

  it('没登记的一家回落成原样的 id，不许变空白', () => {
    // 空白＝让用户以为那一格坏了；原样的 id 至少还认得出是哪家。
    expect(vendorLabelFor('somefuturetool')).toBe('somefuturetool')
    expect(vendorLabelFor('')).toBe('')
  })

  // 11-15：ACP 官方注册表收录的那批客席接进屏上名册（只装不派活）——
  // 屏上写品牌名，**不露内部 id**（机器词不上屏）。
  it('新加的那批候选客席上屏写品牌名，不露内部 id', () => {
    const ids = [
    'agoragentic',
    'auggie',
    'autohand',
    'bob',
    'cortex-code',
    'corust',
    'crow-cli',
    'deepagents',
    'devin',
    'dimcode',
    'dirac',
    'fast-agent',
    'glm',
    'grok',
    'harn',
    'junie',
    'kimchi',
    'kimi',
    'kimi-code',
    'minion-code',
    'vibe',
    'muse-code',
    'nova',
    'pi',
    'poolside',
    'sigit',
    'stakpak',
    'vtcode',
    'deveco',
    ]

    for (const id of ids) {
      expect(vendorLabelFor(id)).not.toBe(id)
      expect(vendorLabelFor(id).length).toBeGreaterThan(0)
    }
  })

})
