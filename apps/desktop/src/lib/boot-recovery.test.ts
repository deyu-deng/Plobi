import { afterEach, describe, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'

import { BOOT_RECOVERY_COPY, renderBootRecoveryCard, runBootstrap } from './boot-recovery'

// R-083 — a broken module graph must leave a CLICKABLE retry card, never a
// white window. The loaders are injected so a rejecting dynamic import can be
// simulated without touching the real module graph.

const CARD_SELECTOR = '.plobi-boot-recovery-wrap'

function mountRoot() {
  const root = document.createElement('div')
  root.id = 'root'
  document.body.append(root)

  return root
}

function flushMicrotasks() {
  return Promise.resolve().then(() => Promise.resolve())
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('runBootstrap', () => {
  it('renders a retry card when the app module graph fails to load', async () => {
    mountRoot()
    const reloadWindow = vi.fn()

    runBootstrap({
      isOverlayWindow: false,
      loadApp: () => Promise.reject(new Error('Cannot find module ./app')),
      loadOverlay: () => Promise.resolve(),
      reloadWindow
    })
    await flushMicrotasks()

    const card = document.querySelector(CARD_SELECTOR)

    expect(card).not.toBeNull()
    expect(card?.textContent).toContain(BOOT_RECOVERY_COPY.en.title)

    const retry = card?.querySelector('button')

    expect(retry?.textContent).toBe(BOOT_RECOVERY_COPY.en.retry)
    retry?.click()
    expect(reloadWindow).toHaveBeenCalledTimes(1)
  })

  it('mounts the app with no card when the graph loads cleanly', async () => {
    mountRoot()
    const loadApp = vi.fn().mockResolvedValue(undefined)

    runBootstrap({
      isOverlayWindow: false,
      loadApp,
      loadOverlay: () => Promise.resolve()
    })
    await flushMicrotasks()

    expect(loadApp).toHaveBeenCalledTimes(1)
    expect(document.querySelector(CARD_SELECTOR)).toBeNull()
  })

  it('keeps the pet-overlay branch out of the app mount and still catches its own failures', async () => {
    mountRoot()
    const loadOverlay = vi.fn().mockRejectedValue(new Error('overlay module broke'))
    const loadApp = vi.fn()

    runBootstrap({
      isOverlayWindow: true,
      loadApp,
      loadOverlay
    })
    await flushMicrotasks()

    expect(loadOverlay).toHaveBeenCalledTimes(1)
    expect(loadApp).not.toHaveBeenCalled()
    // The overlay window is not left white either — it gets the same card.
    expect(document.querySelector(CARD_SELECTOR)).not.toBeNull()
  })

  it('speaks Chinese on a zh machine', async () => {
    mountRoot()
    vi.spyOn(window.navigator, 'language', 'get').mockReturnValue('zh-CN')

    runBootstrap({
      isOverlayWindow: false,
      loadApp: () => Promise.reject(new Error('崩了')),
      loadOverlay: () => Promise.resolve()
    })
    await flushMicrotasks()

    expect(document.querySelector(CARD_SELECTOR)?.textContent).toContain('加载失败')
  })
})

describe('renderBootRecoveryCard', () => {
  it('never stacks a second card', () => {
    renderBootRecoveryCard(new Error('first'))
    renderBootRecoveryCard(new Error('second'))

    expect(document.querySelectorAll(CARD_SELECTOR)).toHaveLength(1)
  })

  it('folds the raw error into a collapsed detail for the developer', () => {
    renderBootRecoveryCard(new Error('boom'))

    const details = document.querySelector('details')

    expect(details?.querySelector('pre')?.textContent).toContain('boom')
  })
})

describe('copy parity with src/i18n', () => {
  it('mirrors the boot.recovery.* keys the normal render path uses', () => {
    // The card cannot import the i18n runtime (that module graph is what just
    // broke), so the strings live twice — this is the guard against drift.
    expect(BOOT_RECOVERY_COPY.en).toEqual({
      description: TRANSLATIONS.en.boot.recovery.description,
      details: TRANSLATIONS.en.boot.recovery.details,
      retry: TRANSLATIONS.en.boot.recovery.retry,
      title: TRANSLATIONS.en.boot.recovery.title
    })
    expect(BOOT_RECOVERY_COPY.zh).toEqual({
      description: TRANSLATIONS.zh.boot.recovery.description,
      details: TRANSLATIONS.zh.boot.recovery.details,
      retry: TRANSLATIONS.zh.boot.recovery.retry,
      title: TRANSLATIONS.zh.boot.recovery.title
    })
  })
})
