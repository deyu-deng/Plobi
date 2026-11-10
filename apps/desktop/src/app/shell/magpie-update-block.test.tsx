import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MagpieUpdateBlock } from './magpie-update-block'

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
  beforeEach(() => {
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
})
