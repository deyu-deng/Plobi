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
  { detail: '3 项', icon: <Codicon name="hubot" size="0.75rem" />, id: 'gateway-health', label: '网关', variant: 'menu' },
  // 没有图标、只带读数的那一格：既要有列，也要把数带出来。
  { detail: '00:12:34', id: 'agenda', label: '日程', variant: 'text' }
]

/**
 * 带面板的那一格（出口／网关）：面板自己带宽度（`w-auto` 跟着面板走，或 `w-72` 那种定宽）。
 * 这一格进「⋯」之后，**那张卡片不许再压一个自己的宽度**——11-07 用户截图：出口那一屏
 * 右边被裁掉一块（选中的那颗只剩一块蓝底、「System proxy」整颗不见了），就是这里
 * 写死的 `w-56`（224px）把面板自己的 384px 裁了。
 */
const PANEL_ITEM: StatusbarItem = {
  id: 'egress',
  label: '出口',
  menuClassName: 'w-auto',
  menuContent: () => <div data-testid="panel">面板</div>,
  variant: 'menu'
}

function openOverflow(items: readonly StatusbarItem[] = ITEMS) {
  // jsdom 量不到宽度 ⇒ 一枚都放不下，整批都进「⋯」，正好照出菜单里的样子。
  render(<StatusbarControls dock inline items={items} />)
  const trigger = screen.getByRole('button', { name: '更多图标' })

  // Radix 的触发键要先认下这一次按下（pointerdown/mousedown），才会在 click 上开。
  fireEvent.pointerDown(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.mouseDown(trigger, { button: 0, detail: 1 })
  fireEvent.pointerUp(trigger, { button: 0, pointerType: 'mouse' })
  fireEvent.mouseUp(trigger, { button: 0, detail: 1 })
  fireEvent.click(trigger, { button: 0, detail: 1 })

  return screen.findByRole('menu')
}

/** 面板那一格点开：清单换成面板本身，菜单不关。 */
async function openPanel() {
  const menu = await openOverflow([...ITEMS, PANEL_ITEM])
  const row = within(menu).getAllByRole('menuitem').find(item => /出口/.test(item.textContent ?? ''))

  fireEvent.click(row as HTMLElement)

  return screen.findByTestId('panel')
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
    const row = within(menu).getAllByRole('menuitem').find(item => /日程/.test(item.textContent ?? ''))

    expect(row?.textContent).toContain('00:12:34')
  })

  it('那张六枚的单子只管左栏底部，吸在窗口角上的经典状态栏照旧全给', () => {
    render(<StatusbarControls items={[...ITEMS, { id: 'cron', label: '定时任务', variant: 'action' }]} />)

    expect(screen.getByText('定时任务')).toBeTruthy()
    expect(screen.getByText('待确认')).toBeTruthy()
  })

  it('带面板的那一格：卡片宽度跟着那一格自己的口径，不许再压一个 w-56', async () => {
    // 11-07 用户截图（出口那一屏右边被裁）：面板自己 384px，卡片却写死 224px ⇒
    // 多出来那一截被 `overflow-x-hidden` 裁掉——选中的那颗只剩一块蓝底，
    // 右边那颗按钮整颗不见了。宽度只有一个来源：那一格说多少就是多少。
    await openPanel()

    const card = document.querySelector('[data-slot="dropdown-menu-content"]')
    expect(card, '面板没在那张卡片里').not.toBeNull()
    expect(card?.className).toContain('w-auto')
    expect(card?.className).not.toMatch(/(^|\s)w-56(\s|$)/)
  })
})
