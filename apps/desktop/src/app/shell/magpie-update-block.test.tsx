import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/*
 * Imported per test, not once for the file: the block keeps its phase at module
 * scope (so a closed menu does not forget an update in flight), which means a
 * fresh module instance is what gives each test a clean slate.
 */
let MagpieUpdateBlock: ComponentType

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      shell: {
        statusbar: {
          magpieUpdate: {
            title: 'magpie 适配器',
            running: (version: string) => `当前 ${version}`,
            unknown: '版本未知',
            check: '检查更新',
            checking: '检查中…',
            available: (version: string) => `可更新到 ${version}`,
            upToDate: '已是最新',
            apply: '更新并重启',
            applying: '正在更新…',
            downloading: (percent: number) => `正在下载 ${percent}%`,
            verifying: '下载完成，正在校验并切换',
            restarting: '正在重启适配器',
            probing: '适配器已启动，正在确认应答',
            applied: (version: string) => `已切到 ${version}，适配器已重启`,
            failed: (reason: string) => `未能更新：${reason}`
          }
        }
      }
    }
  })
}))

const CHECKED_STALE = {
  bytes: 74_000_000,
  latest: '0.9.1',
  ok: true,
  reason: null,
  running: '0.9.0',
  updateAvailable: true
}

const CHECKED_CURRENT = { ...CHECKED_STALE, latest: '0.9.0', updateAvailable: false }

function blockText() {
  return screen.getByTestId('magpie-update-block').textContent ?? ''
}

function bridge(
  check = vi.fn().mockResolvedValue(CHECKED_STALE),
  apply = vi.fn().mockResolvedValue({ ok: true, version: '0.9.1', started: true, port: 5137, reason: null })
) {
  ;(window as { plobiDesktop?: unknown }).plobiDesktop = { applyMagpieUpdate: apply, checkMagpieUpdate: check }

  return { apply, check }
}

describe('MagpieUpdateBlock', () => {
  beforeEach(async () => {
    vi.resetModules()
    MagpieUpdateBlock = (await import('./magpie-update-block')).MagpieUpdateBlock
    delete (window as { plobiDesktop?: unknown }).plobiDesktop
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    delete (window as { plobiDesktop?: unknown }).plobiDesktop
  })

  // 没有桥（老主进程 / 纯浏览器 dev）时宁可不显示，也不给一个按下去没反应的按钮。
  it('renders nothing when the bridge is missing', () => {
    const { container } = render(<MagpieUpdateBlock />)

    expect(container.textContent).toBe('')
  })

  it('shows the running version and the swap button after a check finds an update', async () => {
    const { check } = bridge()

    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))

    await waitFor(() => expect(blockText()).toContain('可更新到 0.9.1'))
    expect(check).toHaveBeenCalledTimes(1)
    expect(blockText()).toContain('当前 0.9.0')
    expect(screen.getByTestId('magpie-apply-update')).toBeTruthy()
  })

  it('stays quiet and offers no swap when the sidecar is already current', async () => {
    bridge(vi.fn().mockResolvedValue(CHECKED_CURRENT))

    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))

    await waitFor(() => expect(blockText()).toContain('已是最新'))
    expect(screen.queryByTestId('magpie-apply-update')).toBeNull()
  })

  it('reports the version it landed on after applying', async () => {
    const { apply } = bridge()

    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))
    await waitFor(() => expect(screen.getByTestId('magpie-apply-update')).toBeTruthy())

    fireEvent.click(screen.getByTestId('magpie-apply-update'))

    await waitFor(() => expect(blockText()).toContain('已切到 0.9.1，适配器已重启'))
    expect(apply).toHaveBeenCalledTimes(1)
    expect(blockText()).toContain('当前 0.9.1')
  })

  // 主进程在摘要不匹配时不动指针、新二进制起不来时退回包内版本，所以这里只是
  // 一句「没换成」，不该渲染成故障。
  it('says why an apply failed without claiming the sidecar is broken', async () => {
    const apply = vi.fn().mockResolvedValue({
      ok: false,
      port: null,
      reason: '新版本已下载，但没能起起来，已退回包内版本',
      started: false,
      version: null
    })

    bridge(undefined, apply)

    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))
    await waitFor(() => expect(screen.getByTestId('magpie-apply-update')).toBeTruthy())

    fireEvent.click(screen.getByTestId('magpie-apply-update'))

    await waitFor(() => expect(blockText()).toContain('未能更新：新版本已下载，但没能起起来，已退回包内版本'))
  })

  // 一次 96MB 的下载如果只有一句「正在更新…」，和卡死没有区别。主进程按 .part
  // 文件大小推真实进度，这里就该如实显示到百分比和阶段。
  it('follows the phases the main process reports while applying', async () => {
    const listeners: ((progress: unknown) => void)[] = []
    let finish: (value: unknown) => void = () => {}

    ;(window as { plobiDesktop?: unknown }).plobiDesktop = {
      applyMagpieUpdate: vi.fn(
        () =>
          new Promise(resolve => {
            finish = resolve
          })
      ),
      checkMagpieUpdate: vi.fn().mockResolvedValue(CHECKED_STALE),
      onMagpieUpdateProgress: (callback: (progress: unknown) => void) => {
        listeners.push(callback)

        return () => {}
      }
    }

    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))
    await waitFor(() => expect(screen.getByTestId('magpie-apply-update')).toBeTruthy())
    fireEvent.click(screen.getByTestId('magpie-apply-update'))

    await waitFor(() => expect(listeners.length).toBe(1))
    act(() => listeners[0]({ message: null, percent: 42, phase: 'download' }))
    await waitFor(() => expect(blockText()).toContain('正在下载 42%'))
    expect(screen.getByTestId('magpie-update-progress')).toBeTruthy()

    act(() => listeners[0]({ message: null, percent: null, phase: 'probe' }))
    await waitFor(() => expect(blockText()).toContain('适配器已启动，正在确认应答'))

    act(() => finish({ ok: true, port: 5137, reason: null, started: true, version: '0.9.1' }))
    await waitFor(() => expect(blockText()).toContain('已切到 0.9.1，适配器已重启'))
  })

  // 这份 UI 挂在状态栏菜单里，菜单一关就卸载。以前一关就什么都没了，用户回来
  // 只看到一块空白，分不清是更新好了还是没开始。
  it('still shows the outcome after the menu closes and reopens', async () => {
    const apply = vi.fn().mockResolvedValue({
      ok: false,
      port: null,
      reason: '适配器没应答',
      started: false,
      version: null
    })

    bridge(undefined, apply)
    render(<MagpieUpdateBlock />)
    fireEvent.click(screen.getByTestId('magpie-check-update'))
    await waitFor(() => expect(screen.getByTestId('magpie-apply-update')).toBeTruthy())
    fireEvent.click(screen.getByTestId('magpie-apply-update'))
    await waitFor(() => expect(blockText()).toContain('未能更新：适配器没应答'))

    cleanup() // the menu closed
    render(<MagpieUpdateBlock />) // and opened again

    await waitFor(() => expect(blockText()).toContain('未能更新：适配器没应答'))
  })
})
