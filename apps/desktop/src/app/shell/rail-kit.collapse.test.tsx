// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RailSection } from './rail-kit'

/**
 * 一段**可折叠**（Qoder 那一层：任务监控的 13 个分区每个都能单独收起）。
 *
 * 单独一档的原因：`rail-kit.test.tsx` 那一档归 `WP-STALE-ASSERT-CATCHUP-2`
 * 在派（别线在飞），不共改同一档（裁定 77 的认领范围门）。
 */

vi.mock('@/i18n', () => ({
  translateNow: (key: string) => key,
  useI18n: () => ({ t: { common: { close: '关闭' } } })
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('rail-kit —— 一段可折叠', () => {
  it('折起时只留小标题，内容不渲染', () => {
    const { rerender } = render(
      <RailSection collapsed={false} collapsible onToggle={() => {}} title="进度">
        <span>内容</span>
      </RailSection>
    )

    expect(screen.getByText('内容')).toBeTruthy()

    rerender(
      <RailSection collapsed collapsible onToggle={() => {}} title="进度">
        <span>内容</span>
      </RailSection>
    )

    expect(screen.queryByText('内容')).toBeNull()
    expect(screen.getByText('进度')).toBeTruthy()
  })

  it('折叠：小标题是个按钮，点了就回调', () => {
    const onToggle = vi.fn()

    render(
      <RailSection collapsible onToggle={onToggle} title="进度">
        <span>内容</span>
      </RailSection>
    )

    fireEvent.click(screen.getByRole('button'))
    expect(onToggle).toHaveBeenCalled()
  })

  it('不可折叠的那一段：标题不是按钮，内容一直在', () => {
    render(
      <RailSection title="进度">
        <span>内容</span>
      </RailSection>
    )

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('内容')).toBeTruthy()
  })
})
