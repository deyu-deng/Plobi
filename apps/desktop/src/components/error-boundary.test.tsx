// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './error-boundary'

/** jsdom 的 location 是只读的，整块换掉才拦得到 reload。 */
function stubLocationReload() {
  const reload = vi.fn()

  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, reload }
  })

  return reload
}

function Boom({ error }: { error: Error }): never {
  throw error
}

function renderBoom(error: Error) {
  render(
    <ErrorBoundary>
      <Boom error={error} />
    </ErrorBoundary>
  )
}

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('ErrorBoundary — 模块图被热更新搞坏时自愈（用户 10-22「点了没反应」）', () => {
  it('悬空引用自动整窗重载一次', () => {
    const reload = stubLocationReload()

    renderBoom(new ReferenceError('AGENT_ICON_CHOICES is not defined'))

    expect(reload).toHaveBeenCalledTimes(1)
    // 门闩落下了：同一窗口内不会连环重载
    expect(window.sessionStorage.getItem('plobi:hmr-stale-reload-once')).toBe('1')
  })

  it('重载之后还坏就不再自动重载（交给错误页，不许循环）', () => {
    const reload = stubLocationReload()
    window.sessionStorage.setItem('plobi:hmr-stale-reload-once', '1')

    renderBoom(new ReferenceError('AgentActionsMenu is not defined'))

    expect(reload).not.toHaveBeenCalled()
    // 用户看得到错误页（不是白屏，也不是无限转圈）
    expect(screen.getByText('AgentActionsMenu is not defined')).toBeTruthy()
  })

  it('真 bug（不是悬空引用）不自动重载——那会把人家的调试状态冲掉', () => {
    const reload = stubLocationReload()

    renderBoom(new TypeError("Cannot read properties of undefined (reading 'map')"))

    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText("Cannot read properties of undefined (reading 'map')")).toBeTruthy()
  })

  it('渲染成功后撤掉门闩：下次真坏还允许再自愈一次', () => {
    window.sessionStorage.setItem('plobi:hmr-stale-reload-once', '1')
    const reload = stubLocationReload()

    render(
      <ErrorBoundary>
        <p>正常的一屏</p>
      </ErrorBoundary>
    )
    // 挂载即清
    expect(window.sessionStorage.getItem('plobi:hmr-stale-reload-once')).toBeNull()

    // 清掉之后再来一次悬空引用，还能自愈
    renderBoom(new ReferenceError('$activeGatewayProfile is not defined'))
    expect(reload).toHaveBeenCalledTimes(1)
  })
})
