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

  it('浮层形态走同一个容器（mode=float），浮层本体仍带 data-testid（拖动定位靠它找本体）', () => {
    const { container } = render(<MonitorRegion agentId="Aura" headerDragProps={{}} />)

    const panel = container.querySelector('[data-monitor-panel]') as HTMLElement

    expect(panel.getAttribute('data-monitor-mode')).toBe('float')
    expect(container.querySelector('[data-testid="dossier-panel"]')).toBeTruthy()
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
