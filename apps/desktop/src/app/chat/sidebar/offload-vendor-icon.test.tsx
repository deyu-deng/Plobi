// @vitest-environment jsdom
/**
 * 厂商图标那一格的判据（用户 11-01：「不要是一个方块加一些文字，而是矢量图标加名词」）。
 *
 * 钉三件事：**不再出现字母方块**（10-22 那版的首字母徽标被点名换掉）、**每家都有一枚
 * 矢量字形**（没登记品牌的一家也不许空白）、**不臆造品牌色**（以"不带内联颜色"为断言）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { OffloadVendorIcon } from './offload-vendor-icon'

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
})
