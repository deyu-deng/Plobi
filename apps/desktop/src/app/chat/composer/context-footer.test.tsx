// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import {
  setBusy,
  setConnection,
  setCurrentCwd,
  setCurrentUsage,
  setSessionStartedAt,
  setTurnStartedAt
} from '@/store/session'

import { ComposerContextFooter } from './context-footer'

/**
 * 输入框下面那一行（**上下文条**）——用户 11-28 00:1x：
 * 「这个时长我希望放到对话框下面的上下文条的左边。并且顺便把对话框下面的组件变的可交互。」
 *
 * 判据钉的是**关系**：时长**在那一行的左边**、工作目录与本地·远程**点得动**、
 * 右边那枚容量条**照旧**（没被这一刀碰坏）。
 */
vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway: vi.fn() })
}))

vi.mock('@/app/shell/context-usage-panel', () => ({
  ContextUsagePanel: () => <div data-testid="context-usage-panel" />
}))

const openExternal = vi.fn()

function renderFooter() {
  return render(
    <I18nProvider
      configClient={{ getConfig: async () => ({}), saveConfig: async () => ({ ok: true }) }}
      initialLocale="zh"
    >
      <MemoryRouter initialEntries={['/agent/butler']}>
        <Routes>
          <Route
            element={
              <>
                <ComposerContextFooter />
                <LocationProbe />
              </>
            }
            path="*"
          />
        </Routes>
      </MemoryRouter>
    </I18nProvider>
  )
}

/** 路由落在哪儿（点「本地／远程」那一下要看得见它去了设置里的网关那一段）。 */
function LocationProbe() {
  const location = useLocation()

  return <div data-testid="route">{`${location.pathname}${location.search}`}</div>
}

describe('输入框下面那条上下文条', () => {
  beforeEach(() => {
    openExternal.mockReset()
    Object.defineProperty(window, 'plobiDesktop', { configurable: true, value: { openExternal } })
    setConnection({ mode: 'local' } as never)
    setCurrentCwd('D:/Projects/Aura')
    setCurrentUsage({ context_max: 1000, context_percent: 42, total: 10, context_used: 420 } as never)
    setBusy(false)
    setTurnStartedAt(null)
    setSessionStartedAt(null)
  })

  afterEach(() => {
    cleanup()
  })

  it('**时长在那一行的左边**（这一轮 ＋ 会话各一格）', () => {
    renderFooter()

    const row = document.querySelector('[data-slot="composer-footer"]') as HTMLElement
    const turn = row.querySelector('[data-slot="composer-footer-turn"]') as HTMLElement
    const session = row.querySelector('[data-slot="composer-footer-session"]') as HTMLElement

    expect(turn).toBeTruthy()
    expect(session).toBeTruthy()
    // 左边 = 排在工作目录与容量条**前面**。
    expect(turn.compareDocumentPosition(session) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(session.compareDocumentPosition(row.querySelector('[data-slot="composer-footer-cwd"]')!) &
      Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('这一轮**跑的时候**才有数；会话时长**一直**在', () => {
    setTurnStartedAt(Date.now() - 5_000)
    setSessionStartedAt(Date.now() - 65_000)

    renderFooter()

    expect(document.querySelector('[data-slot="composer-footer-turn"]')?.textContent).toMatch(/0:0\d/)
    expect(document.querySelector('[data-slot="composer-footer-session"]')?.textContent).toMatch(/1:0\d/)
  })

  it('**工作目录那一枚点得动**：本地 ⇒ 在系统文件管理器里打开那个目录', () => {
    renderFooter()

    fireEvent.click(screen.getByTitle('打开这个目录'))

    expect(openExternal).toHaveBeenCalledTimes(1)
    expect(openExternal.mock.calls[0][0]).toContain('D:/Projects/Aura')
    expect(openExternal.mock.calls[0][0].startsWith('file:///')).toBe(true)
  })

  it('远端时那个目录**在另一台机器上** ⇒ 那一枚不是按钮（不假装能打开）', () => {
    setConnection({ mode: 'remote', baseUrl: 'http://box:8080' } as never)

    renderFooter()

    expect(document.querySelector('[data-slot="composer-footer-cwd"]')).toBeNull()
    expect(screen.getByTitle('这是远端的目录')).toBeTruthy()
  })

  it('**本地／远程那一枚点得动**：跳到设置里的网关那一段', () => {
    renderFooter()

    fireEvent.click(screen.getByTitle('网关'))

    expect(screen.getByTestId('route').textContent).toBe('/settings?tab=gateway')
  })

  it('右边那枚**上下文容量条照旧**（点它还是那个面板）', () => {
    renderFooter()

    const trigger = screen.getByLabelText('上下文')

    expect(trigger).toBeTruthy()
    expect(trigger.textContent).toContain('42%')
  })
})
