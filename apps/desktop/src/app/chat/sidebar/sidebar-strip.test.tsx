import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Agent } from '@/app/console/types'

import { SidebarStrip, STRIP_HOVER_CLOSE_MS } from './sidebar-strip'

/**
 * 收起态那条 40px 图标条（用户 10-31 定的形状）。这一档钉两条他 11-01 报的缺陷：
 * ① 「新建 Agent」渲染成了英文单词 `add`——图标传成了字符串。屏上任何一枚都不该
 *    把内部图标名当文案印出来。
 * ② 分类图标一悬停，弹窗闪个不停。根因在 Radix 的 DropdownMenu 默认 **modal**：
 *    一打开就把 `document.body` 的 pointer-events 设成 `none`（dismissable-layer），
 *    底下那只鼠标立刻收到一次 pointerleave，于是「开 → 被关 → 再开」自己跟自己打。
 *    第二条就是照这个根因写的判据。
 */

const onCreateAgent = vi.fn()
const onNavigate = vi.fn()
const onNavigateAgent = vi.fn()

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      console: {
        agentCategories: { projects: '项目', research: '研究' },
        agentsEmptyDesc: '还没有 Agent',
        newAgent: '新建 Agent'
      },
      sidebar: { nav: {} }
    }
  })
}))

vi.mock('../../desktop-controller-utils', () => ({
  secretaryShortName: (_id: string, name: string) => name
}))

// 底部收敛区是另一档的事（statusbar-controls 自己测），这里只留个占位。
vi.mock('@/app/shell/statusbar-controls', () => ({
  StatusbarControls: () => <div data-stub="statusbar" />
}))

const AGENT = {
  category: 'projects',
  id: 'nymo',
  name: 'Nymo',
  navigable: true,
  profile: 'nymo',
  status: 'idle',
  todayCalls: 0
} as unknown as Agent

function renderStrip(
  groups: { agents: Agent[]; category: string }[] = [{ agents: [AGENT], category: 'projects' }]
) {
  return render(
    <>
      {/* 光标路过左栏时，用户可能正在这玩意儿里打字。 */}
      <input aria-label="输入框" />
      <SidebarStrip
        activeAgentId={null}
        agentActions={[]}
        footerItems={[]}
        groups={groups}
        onCreateAgent={onCreateAgent}
        onNavigate={onNavigate}
        onNavigateAgent={onNavigateAgent}
        sharedItems={[]}
      />
    </>
  )
}

/**
 * React 的 `onPointerEnter` 是从原生 `pointerover` + `relatedTarget` 推出来的，
 * 光发 `pointerenter` 打不到处理器，所以两种都发。
 */
function hover(element: Element) {
  fireEvent.pointerOver(element, { bubbles: true, relatedTarget: document.body })
  fireEvent.pointerEnter(element, { relatedTarget: document.body })
}

function unhover(element: Element) {
  fireEvent.pointerOut(element, { bubbles: true, relatedTarget: document.body })
  fireEvent.pointerLeave(element, { relatedTarget: document.body })
}

afterEach(() => {
  cleanup()
  document.body.style.pointerEvents = ''
  vi.clearAllMocks()
})

describe('左栏收起态图标条', () => {
  it('「新建 Agent」是一枚图标，不是文字', () => {
    renderStrip()

    const button = screen.getByRole('button', { name: '新建 Agent' })

    expect(button.textContent, '屏上印了字：图标名被当文案渲染').toBe('')
    expect(button.querySelector('.codicon-add'), '没有 codicon-add 这枚图标').not.toBeNull()
  })

  it('悬停弹出的分类面板不许锁住全页的 pointer-events', async () => {
    renderStrip()

    hover(screen.getByRole('button', { name: '项目' }))

    await screen.findByRole('menu')

    // 锁了 = 触发键当场收到 pointerleave = 弹窗开开关关闪个不停。
    expect(document.body.style.pointerEvents, 'body 被锁成 pointer-events:none').toBe('')
  })

  it('指针从图标移到面板上不算离开，出了两边才关', async () => {
    renderStrip()

    const trigger = screen.getByRole('button', { name: '项目' })
    hover(trigger)
    const menu = await screen.findByRole('menu')

    // 真实顺序：浏览器先给旧位置 pointerout，再给新位置 pointerover。
    unhover(trigger)
    hover(menu)
    await new Promise(resolve => {
      setTimeout(resolve, STRIP_HOVER_CLOSE_MS + 150)
    })
    expect(menu.getAttribute('data-state'), '移到面板上却被关掉了').toBe('open')

    unhover(menu)
    await waitFor(() => {
      const next = screen.queryByRole('menu')

      expect(next === null || next.getAttribute('data-state') === 'closed').toBe(true)
    })
  })

  it('悬停开的面板不许把焦点从输入框抢走', async () => {
    renderStrip()

    const input = screen.getByLabelText('输入框')

    input.focus()
    hover(screen.getByRole('button', { name: '项目' }))

    // Radix 的 FocusScope 在挂载后的下一帧抓焦点，所以这里要多等几帧。
    await screen.findByRole('menu')
    await new Promise(resolve => {
      setTimeout(resolve, 120)
    })

    expect(document.activeElement, '焦点被面板抢走了：接着打字会打进方向键搜索').toBe(input)
  })

  it('整条同时只开一个分类面板', async () => {
    renderStrip([
      { agents: [AGENT], category: 'projects' },
      { agents: [AGENT], category: 'research' }
    ])

    hover(screen.getByRole('button', { name: '项目' }))
    await screen.findByRole('menu')

    // 手一抖划到下一类：旧的关掉、新的开来，不许两份叠在同一个位置。
    hover(screen.getByRole('button', { name: '研究' }))

    await waitFor(() => {
      const opened = screen.queryAllByRole('menu').filter(item => item.getAttribute('data-state') === 'open')

      expect(opened).toHaveLength(1)
      expect(opened[0]?.textContent).toContain('研究')
    })
  })
})
