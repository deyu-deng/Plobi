import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { triggerHaptic } from '@/lib/haptics'
import { $hapticsMuted, setHapticsMuted } from '@/store/haptics'

import { HapticsRow } from './haptics-row'

vi.mock('@/lib/haptics', () => ({
  triggerHaptic: vi.fn()
}))

/**
 * 触感反馈这一格从左上角那条图标条搬进设置（用户 11-02 拍「底部只留六枚」）。
 * 搬走的同时它得还在人手里，所以这一档钉两条：开关的方向与标题是同一句、
 * 取消静音那一下真的震。屏幕上的字从目录取，断言也从目录取——换语言不许把测试换红。
 */
const copy = TRANSLATIONS.en.titlebar

afterEach(() => {
  cleanup()
  setHapticsMuted(false)
  vi.clearAllMocks()
})

describe('设置里的触感反馈那一格', () => {
  it('开关按的是「静音」那一头，与标题同一句', () => {
    setHapticsMuted(true)
    render(<HapticsRow />)

    expect(screen.getByText(copy.muteHaptics)).toBeTruthy()
    expect(screen.getByRole('switch').getAttribute('data-state')).toBe('checked')

    setHapticsMuted(false)
    cleanup()
    render(<HapticsRow />)

    expect(screen.getByRole('switch').getAttribute('data-state')).toBe('unchecked')
  })

  it('放开静音的那一下当场震一次，让人知道这格管的是哪件事', () => {
    setHapticsMuted(true)
    render(<HapticsRow />)

    fireEvent.click(screen.getByRole('switch'))

    expect($hapticsMuted.get()).toBe(false)
    expect(vi.mocked(triggerHaptic)).toHaveBeenCalledWith('success')
  })
})
