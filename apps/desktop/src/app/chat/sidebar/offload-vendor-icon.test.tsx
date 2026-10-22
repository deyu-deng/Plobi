// @vitest-environment jsdom
/**
 * 厂商徽标那一格的判据（用户 10-22：「我没有看到界面上有显示 Qoder 的图标」）。
 *
 * 钉三件事：**每家各一枚**（不许再是一家一个通用火箭）、**没登记的一家也有形状**
 * （回落首字母，不是空白方块）、**没有可靠品牌色来源时我们不臆造**（以"徽标不带内联
 * 颜色"为断言——有人把臆造的色写进来，这条就红，逼他写清来源）。
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { hasVendorIcon, OffloadVendorAvatar, vendorMonogramFor } from './offload-vendor-icon'

afterEach(cleanup)

describe('offload vendor avatar', () => {
  it('gives each registered vendor its own badge', () => {
    render(
      <div>
        <OffloadVendorAvatar vendor="codebuddy" />
        <OffloadVendorAvatar vendor="qoder" />
      </div>,
    )

    // CodeBuddy 有官方字形（simple-icons 收了）；Qoder 还没有 ⇒ 首字母。两者不是同一枚图。
    expect(screen.getByTestId('offload-vendor-glyph-codebuddy')).toBeTruthy()
    expect(screen.getByTestId('offload-vendor-monogram-qoder')).toBeTruthy()
  })

  it('falls back to the name initial instead of a blank square', () => {
    expect(hasVendorIcon('somefuturetool')).toBe(false)
    expect(vendorMonogramFor('somefuturetool')).toBe('S')
    render(<OffloadVendorAvatar vendor="somefuturetool" />)

    expect(screen.getByTestId('offload-vendor-monogram-somefuturetool').textContent).toBe('S')
  })

  it('never renders an empty label when the ledger has no vendor name', () => {
    expect(vendorMonogramFor('')).toBe('?')
    expect(vendorMonogramFor('   ')).toBe('?')
  })

  it('carries no invented inline brand color', () => {
    render(<OffloadVendorAvatar vendor="qoder" />)

    // 颜色只走主题令牌（class）。写死一个十六进制色＝替那个品牌认领一个它自己没用的身份，
    // 而这一格的三个品牌我们都没有可靠取色来源（React 包装不带 hex）。
    expect(screen.getByTestId('offload-vendor-monogram-qoder').getAttribute('style')).toBe(null)
  })
})
