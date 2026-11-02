import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Codicon } from '@/components/ui/codicon'

import { StatusbarControls, type StatusbarItem } from './statusbar-controls'

/**
 * 收起态那条窄条只剩一枚「⋯」，底下所有格子都挤进这一个菜单（用户 11-02 三问）。
 * 这一档先只管他指出的两处**缺陷**，不管"该留几枚"那一问（那要他拍）：
 * ① 图标没对齐：菜单里 SVG 被 `[&_svg]:size-4` 撑成 16px，而 Codicon 是字体字形，
 *    不吃这条 ⇒ 每行的图标各自一个宽度，文字跟着东倒西歪；没有图标的那一行更惨，
 *    文字直接顶到最左。
 * ② 「Session」这一行莫名其妙：它是读数（跑多久了），`detail` 才是那个数，
 *    而菜单只画了 `label` ⇒ 屏幕上剩下一个孤零零的词。
 */

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn()
}))

// 图标条自己量宽度（ResizeObserver），jsdom 没有——照 language-switcher 那一档补个壳。
class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: { shell: { statusbar: { more: '更多图标' } } }
  })
}))

const ITEMS: readonly StatusbarItem[] = [
  { icon: <Codicon name="book" size="0.75rem" />, id: 'mind-pending', label: '待确认', variant: 'action' },
  { icon: <span data-testid="svg-icon" />, id: 'cron', label: '定时任务', variant: 'action' },
  { detail: '00:12:34', id: 'session-timer', label: '会话', variant: 'text' }
]

function openOverflow() {
  // jsdom 量不到宽度 ⇒ 一枚都放不下，整批都进「⋯」，正好照出菜单里的样子。
  render(<StatusbarControls dock inline items={ITEMS} />)
  const trigger = screen.getByRole('button', { name: '更多图标' })

  // Radix 的触发键要先认下这一次按下（pointerdown/mousedown），才会在 click 上开。
  fireEvent.pointerDown(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.mouseDown(trigger, { button: 0, detail: 1 })
  fireEvent.pointerUp(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.mouseUp(trigger, { button: 0, detail: 1 })
  fireEvent.click(trigger, { button: 0, detail: 1 })

  return screen.findByRole('menu')
}

afterEach(() => {
  cleanup()
})

describe('左栏「⋯」里的那些行', () => {
  it('每一行都有一条同样宽的图标列，文字才排得齐', async () => {
    const menu = await openOverflow()
    const rows = within(menu).getAllByRole('menuitem')

    expect(rows).toHaveLength(3)

    rows.forEach(row => {
      const column = row.firstElementChild

      expect(column, '这一行开头没有图标列').not.toBeNull()
      // 同一列：固定 16px、不参与文字排布（jsdom 量不到像素，判据落在类名上）。
      expect(column?.className).toContain('size-4')
      expect(column?.className).toContain('shrink-0')
    })
  })

  it('读数那一行把数也带出来，不留一个孤零零的词', async () => {
    const menu = await openOverflow()
    const row = within(menu).getAllByRole('menuitem').find(item => /会话/.test(item.textContent ?? ''))

    expect(row?.textContent).toContain('00:12:34')
  })
})
