import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopReleaseInfo } from '@/global'

// The store reaches the backend through `@/plobi`; the overlay itself only
// needs the bridge, which this test drives through the atoms.
vi.mock('@/plobi', () => ({
  checkPlobiUpdate: vi.fn(async () => ({})),
  getActionStatus: vi.fn(),
  getPlobiConfigRecord: vi.fn(async () => ({})),
  savePlobiConfig: vi.fn(async () => ({ ok: true })),
  updatePlobi: vi.fn()
}))

const { UpdatesOverlay } = await import('./updates-overlay')

const {
  $desktopRelease,
  $releaseInstalling,
  $updateChecking,
  $updateOverlayOpen,
  $updateOverlayTarget,
  $updateSource,
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

describe('updates overlay: the desktop release view', () => {
  beforeEach(() => {
    resetUpdateApplyState()
    $updateOverlayTarget.set('client')
    $updateOverlayOpen.set(true)
    $updateChecking.set(false)
    $updateStatus.set(null)
    $updateSource.set('checkout')
    $releaseInstalling.set(false)
    $desktopRelease.set(null)
  })

  afterEach(() => {
    cleanup()
    $updateOverlayOpen.set(false)
    $desktopRelease.set(null)
    delete (window as unknown as { plobiDesktop?: unknown }).plobiDesktop
  })

  it('offers the one-click upgrade when a newer desktop build exists', () => {
    $desktopRelease.set(release())

    render(<UpdatesOverlay />)

    expect(screen.getByText(en.settings.about.updateReadyVersion('0.3.1'))).toBeTruthy()
    expect(screen.getByText(en.updates.availableBody)).toBeTruthy()
    expect(screen.getByRole('button', { name: en.settings.about.upgradeTo('0.3.1') })).toBeTruthy()
  })

  it('links to the release notes of that version', () => {
    $desktopRelease.set(release())

    render(<UpdatesOverlay />)

    const link = screen.getByRole('link', { name: en.settings.about.releaseNotes }) as HTMLAnchorElement

    expect(link.getAttribute('href')).toBe('https://github.com/deyu-deng/Plobi/releases/tag/v0.3.1')
  })

  it('does not offer the upgrade when this build cannot update itself', () => {
    $desktopRelease.set(release({ canUpdate: false, reason: 'manual-required' }))

    render(<UpdatesOverlay />)

    expect(screen.queryByRole('button', { name: en.settings.about.upgradeTo('0.3.1') })).toBeNull()
    expect(screen.getByText(en.updates.unsupportedMessage)).toBeTruthy()
  })

  it('keeps the ordinary view when there is no newer release', () => {
    $desktopRelease.set(release({ newer: false, latest: null }))
    $updateStatus.set({
      supported: true,
      behind: 0,
      updateAvailable: false,
      targetSha: undefined,
      fetchedAt: 0
    })

    render(<UpdatesOverlay />)

    expect(screen.getByText(en.updates.allSetTitle)).toBeTruthy()
    expect(screen.queryByRole('button', { name: en.settings.about.upgradeTo('0.3.1') })).toBeNull()
  })

  it('retries the release install itself after a failure, not the checkout update', async () => {
    const releaseInstall = vi.fn().mockResolvedValue({ ok: false, reason: 'download-failed', message: '下载失败' })

    const apply = vi.fn()

    ;(window as unknown as { plobiDesktop: unknown }).plobiDesktop = { updates: { apply, releaseInstall } }
    $desktopRelease.set(release())

    render(<UpdatesOverlay />)

    fireEvent.click(screen.getByRole('button', { name: en.settings.about.upgradeTo('0.3.1') }))

    await waitFor(() => expect(screen.getByText(en.updates.errorTitle)).toBeTruthy())
    expect(releaseInstall).toHaveBeenCalledTimes(1)
    expect(apply).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: en.updates.tryAgain }))

    await waitFor(() => expect(releaseInstall).toHaveBeenCalledTimes(2))
    // The retry must not fall through to the checkout update.
    expect(apply).not.toHaveBeenCalled()
  })
})
