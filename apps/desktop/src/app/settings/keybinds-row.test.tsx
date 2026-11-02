import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { toggleKeybindPanel } from '@/store/keybinds'

import { KeybindsRow } from './keybinds-row'

vi.mock('@/lib/haptics', () => ({
  triggerHaptic: vi.fn()
}))

vi.mock('@/store/keybinds', () => ({
  toggleKeybindPanel: vi.fn()
}))

/**
 * 快捷键那一格从左上角的图标条搬进设置·外观（用户 11-02 拍「底部只留六枚」）。
 * 搬走的同时它得还在鼠标手里——这一档钉的就是"这一行点得动、点开是那张键位表"。
 * 屏上的字从目录取，断言也从目录取。
 */
const copy = TRANSLATIONS.en.titlebar

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('设置里的快捷键那一格', () => {
  it('整行点得动，点开就是那张键位表', () => {
    render(<KeybindsRow />)

    fireEvent.click(screen.getByRole('button', { name: copy.openKeybinds }))

    expect(toggleKeybindPanel).toHaveBeenCalledTimes(1)
  })
})
