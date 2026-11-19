// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $monitorFace, $monitorPinned } from '@/store/layout'

// 那一面自己的内容各写各的 —— 这一档只关心**壳**：头、内容按当前那一面换、图钉。
vi.mock('@/app/dossier/dossier-panel', () => ({
  DossierFace: () => <div data-testid="dossier-face" />
}))
vi.mock('./pending-tickets', () => ({
  PendingTicketsFace: () => <div data-testid="pending-face" />
}))
vi.mock('@/app/mind-pending-overlay', () => ({
  MindPendingFace: () => <div data-testid="mind-pending-face" />
}))

const { MonitorRegion } = await import('./monitor-region')

beforeEach(() => {
  $monitorFace.set('dossier')
  $monitorPinned.set(true)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('那一张卡（档案／待放行／写脑待批共用）', () => {
  it('显示当前那一面的内容，头上写的是这一面叫什么', () => {
    const { container } = render(<MonitorRegion agentId="Aura" />)

    expect(screen.getByTestId('dossier-face')).toBeTruthy()
    expect(container.querySelector('[data-monitor-card-header]')).toBeTruthy()
    expect(container.textContent).toMatch(/档案|Dossier/)
  })

  it('换面 ⇒ 内容跟着换，壳还是那一个', () => {
    const { container, rerender } = render(<MonitorRegion agentId="Aura" />)

    $monitorFace.set('pending')
    rerender(<MonitorRegion agentId="Aura" />)

    expect(screen.getByTestId('pending-face')).toBeTruthy()
    expect(screen.queryByTestId('dossier-face')).toBeNull()
    expect(container.querySelectorAll('[data-monitor-panel]')).toHaveLength(1)

    $monitorFace.set('mind-pending')
    rerender(<MonitorRegion agentId="Aura" />)

    expect(screen.getByTestId('mind-pending-face')).toBeTruthy()
    expect(container.querySelectorAll('[data-monitor-panel]')).toHaveLength(1)
  })

  it('固定列形态：本体没有描边（Qoder 那边靠字色与留白分层）', () => {
    const { container } = render(<MonitorRegion agentId="Aura" />)

    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel).toBeTruthy()
    expect(panel.getAttribute('data-monitor-mode')).toBe('column')
    expect(panel.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
  })

  it('浮层形态挂到 body 上（portal）：祖先的 overflow-hidden 裁不到它，它也在最上层', () => {
    const { container } = render(<MonitorRegion agentId="Aura" headerDragProps={{}} />)

    // 浮层**不在**对话卡那个容器里（原来挂在里面 ⇒ 被 `overflow-hidden` 裁掉）
    expect(container.querySelector('[data-monitor-panel]')).toBeNull()

    const layer = document.querySelector('[data-monitor-float-layer]') as HTMLElement

    expect(layer).toBeTruthy()
    expect(layer.className).toContain('fixed')
    expect(layer.className).toContain('z-[1000]')
    expect(layer.parentElement).toBe(document.body)

    const panel = document.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.getAttribute('data-monitor-mode')).toBe('float')
    // 浮层本体仍带 data-testid（拖动定位靠它找本体）
    expect(document.querySelector('[data-testid="dossier-panel"]')).toBeTruthy()
  })

  it('浮层的位置是**视口坐标**（left/top 落在那层 fixed 壳上）', () => {
    render(<MonitorRegion agentId="Aura" headerDragProps={{}} />)

    const panel = document.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.style.left).not.toBe('')
    expect(panel.style.top).not.toBe('')
  })

  it('卡头只有「档案」两个字：没有那一枚矢量图标', () => {
    const { container } = render(<MonitorRegion agentId="Aura" />)

    const cardHeader = container.querySelector('[data-monitor-card-header]') as HTMLElement

    // 头里那枚矢量图标托不在了（图标留在抬头右上角那几枚**入口**上）
    expect(cardHeader.querySelector('[data-monitor-icon]')).toBeNull()
    expect(container.textContent).toMatch(/档案|Dossier/)
  })

  it('卡头下面没有那条线（Qoder 第二张那样：纯文字 ＋ 右上角图钉）', () => {
    const { container } = render(<MonitorRegion agentId="Aura" />)

    const header = container.querySelector('[data-monitor-header]') as HTMLElement

    expect(header).toBeTruthy()
    expect(header.className).not.toMatch(/(^|\s)border(-[trblxy])?(\s|$)/)
  })

  it('并排列不占顶栏：从顶栏下一行开始，并把顶栏那道线接过去', () => {
    const { container } = render(<MonitorRegion agentId="Aura" />)

    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement
    const row = panel.querySelector('[data-monitor-topbar-row]') as HTMLElement

    expect(row).toBeTruthy()
    // 它是**头上面**那一行（头在顶栏下一行）
    expect(panel.firstElementChild).toBe(row)
    expect(row.className).toContain('h-(--card-header-height)')
    expect(row.className).toContain('border-b')
    expect(row.className).toContain('border-(--ui-stroke-tertiary)')
  })

  it('那一枚图钉切的是这张卡的形状（三面都有它）', () => {
    render(<MonitorRegion agentId="Aura" />)

    const pin = screen.getByTestId('dossier-pin')

    expect(pin.getAttribute('aria-label')).toMatch(/取消固定|Unpin/)

    fireEvent.click(pin)

    expect($monitorPinned.get()).toBe(false)
  })

  it('窄形态不出现那枚图钉（那里没有并排列可切）', () => {
    render(<MonitorRegion agentId="Aura" showPin={false} />)

    expect(screen.queryByTestId('dossier-pin')).toBeNull()
  })
})
