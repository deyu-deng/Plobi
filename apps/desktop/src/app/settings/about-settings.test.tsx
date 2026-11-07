import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopReleaseInfo } from '@/global'

vi.mock('@/plobi', () => ({
  checkPlobiUpdate: vi.fn(async () => ({})),
  getActionStatus: vi.fn(),
  getPlobiConfigRecord: vi.fn(async () => ({})),
  savePlobiConfig: vi.fn(async () => ({ ok: true })),
  updatePlobi: vi.fn()
}))

const { AboutSettings } = await import('./about-settings')

const {
  $desktopRelease,
  $desktopVersion,
  $releaseInstalling,
  $updateApply,
  $updateChecking,
  $updateStatus,
  resetUpdateApplyState
} = await import('@/store/updates')

const { en } = await import('@/i18n/en')

const release = (over: Partial<DesktopReleaseInfo> = {}): DesktopReleaseInfo => ({
  ok: true,
  current: '0.3.0',
  latest: '0.3.1',
  tag: 'v0.3.1',
  htmlUrl: 'https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1',
  newer: true,
  canUpdate: true,
  assetName: 'Plobi-0.3.1-win-x64.exe',
  assetUrl: 'https://example.invalid/Plobi-0.3.1-win-x64.exe',
  assetSize: 1024,
  reason: null,
  status: 200,
  checkedAt: 0,
  ...over
})

describe('About panel: the desktop release line', () => {
  beforeEach(() => {
    resetUpdateApplyState()
    $desktopVersion.set(null)
    $desktopRelease.set(null)
    $releaseInstalling.set(false)
    $updateChecking.set(false)
    $updateStatus.set({
      supported: true,
      behind: 0,
      updateAvailable: false,
      fetchedAt: 0
    })
  })

  afterEach(() => {
    cleanup()
    $desktopRelease.set(null)
  })

  it('offers the upgrade when the shell itself is behind', () => {
    $desktopRelease.set(release())

    render(<AboutSettings />)

    expect(screen.getByText(en.settings.about.updateReadyVersion('0.3.1'))).toBeTruthy()
    expect(screen.getByRole('button', { name: en.settings.about.upgradeTo('0.3.1') })).toBeTruthy()
  })

  it('does not claim "you are on the latest version" when the release check failed', () => {
    // 壳那侧这一查没跑成（被限流／连不上）⇒ 说「已是最新」是替一个没跑成的
    // 查法背书：那是「我们没看见」，不是「没有」。
    $desktopRelease.set(
      release({
        ok: false,
        newer: false,
        latest: null,
        tag: null,
        canUpdate: false,
        reason: 'rate-limited',
        status: 403
      })
    )

    render(<AboutSettings />)

    expect(screen.getByText(en.settings.about.cantReach)).toBeTruthy()
    expect(screen.queryByText(en.settings.about.onLatest)).toBeNull()
    // The diagnostic line names what actually happened.
    expect(screen.getByText(/rate-limited/)).toBeTruthy()
  })

  it('a packaged install (no agent checkout) is a normal state, not an error', () => {
    // 装机版的后端是**从包里装的**（没有 .git）⇒「不是 git 检出」是正常状态。
    // 那一格该说壳那边：查过了就是「已是最新」，而且「立即检查」必须能按
    // （壳那侧的检查与 git 无关）。
    $updateStatus.set({
      supported: false,
      reason: 'not-a-git-checkout',
      message: "C:\\Users\\tester\\Plobi\\plobi-agent isn't a git checkout — desktop self-update only runs against a source install.",
      behind: 0,
      updateAvailable: false
    })
    $desktopRelease.set(release({ newer: false, latest: '0.3.0' }))

    render(<AboutSettings />)

    expect(screen.getByText(en.settings.about.onLatest)).toBeTruthy()
    expect(screen.queryByText(/git checkout/)).toBeNull()
    // 屏上不许出现开发态那对「Branch · Commit」。
    expect(screen.queryByText(/Branch/)).toBeNull()

    const checkButton = screen.getByText(en.settings.about.checkNow).closest('button')
    expect(checkButton?.disabled).toBe(false)
  })
})
