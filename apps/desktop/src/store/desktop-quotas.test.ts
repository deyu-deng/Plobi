import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $connectedDesktopApps,
  applyDiscoveredQuotaApps,
  DESKTOP_QUOTA_MODELS,
  desktopQuotaProviders,
  firstSelectableQuotaModel,
  markConnected,
  quotaSelectionVerdict,
  type QuotaAppFrame
} from './desktop-quotas'

/** One app exactly as the shell pushes it: the hub's verdict, not a bare name list. */
function frame(id: string, over: Partial<QuotaAppFrame> = {}): QuotaAppFrame {
  return {
    answered: true,
    apiKey: 'sk-local-dev-key',
    baseUrl: 'http://127.0.0.1:8000/v1',
    blocked: {},
    id,
    models: [],
    ...over
  }
}

// desktopQuotaProviders() is the single combinator behind all three model-picker
// surfaces (model-menu-panel, model-picker, chat-runtime), so these are the rules
// that actually decide what the user can pick — not the per-surface cosmetics.
describe('desktopQuotaProviders', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('never offers the gateway demo channel as if it were quota', () => {
    markConnected({ id: 'mock', models: ['echo', 'static'] })
    markConnected({ id: 'workbuddy', models: ['glm-5.3'] })

    expect(desktopQuotaProviders().map(provider => provider.slug)).toEqual(['workbuddy'])
  })

  it('prefers the live gateway catalog over the checked-in seed', () => {
    markConnected({ id: 'antigravity', models: ['glm-5.9-pro-max'] })

    const row = desktopQuotaProviders().find(provider => provider.slug === 'antigravity')

    expect(row?.models).toEqual(['glm-5.9-pro-max'])
    // the point of the test: a model only the live gateway knows must be offered,
    // and a seed entry the gateway no longer serves must not shadow it
    expect(DESKTOP_QUOTA_MODELS.antigravity).not.toContain('glm-5.9-pro-max')
  })

  it('falls back to the seed only before any gateway read has populated the app', () => {
    markConnected({ id: 'cursor', models: [] })

    const row = desktopQuotaProviders().find(provider => provider.slug === 'cursor')

    expect(row?.models).toEqual(DESKTOP_QUOTA_MODELS.cursor)
  })

  it('drops a disconnected app even when it has models cached', () => {
    $connectedDesktopApps.set({
      workbuddy: {
        id: 'workbuddy',
        name: 'Workbuddy',
        connected: false,
        models: ['glm-5.3']
      }
    })

    expect(desktopQuotaProviders()).toEqual([])
  })

  it('marks every surfaced model free, because it rides the app subscription', () => {
    markConnected({ id: 'workbuddy', models: ['glm-5.3', 'kimi-k3-1'] })

    const [row] = desktopQuotaProviders()

    expect(row.free_tier).toBe(true)
    expect(Object.values(row.pricing ?? {})).toEqual([{ free: true }, { free: true }])
    expect(row.total_models).toBe(2)
  })
})

// applyDiscoveredQuotaApps is the "connect once, auto-connect on every launch"
// path: the main process pushes the live hub catalog and apps become selectable
// with no manual click. These assert that contract end-to-end into the picker.
describe('applyDiscoveredQuotaApps', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('surfaces a hub-served app in the picker without any manual connect', () => {
    applyDiscoveredQuotaApps([
      frame('workbuddy', { models: ['hy3', 'glm-5.3'] })
    ])

    const [row] = desktopQuotaProviders()

    expect(row.slug).toBe('workbuddy')
    expect(row.models).toEqual(['hy3', 'glm-5.3'])
    // the hub's coordinates must ride through so the submit path routes to :8000
    expect(row.authenticated).toBe(true)
    expect($connectedDesktopApps.get().workbuddy?.baseUrl).toBe('http://127.0.0.1:8000/v1')
    expect($connectedDesktopApps.get().workbuddy?.apiKey).toBe('sk-local-dev-key')
  })

  it('never lets a pushed demo channel reach the selector', () => {
    applyDiscoveredQuotaApps([
      frame('mock', { models: ['echo'] }),
      frame('workbuddy', { models: ['hy3'] })
    ])

    expect(desktopQuotaProviders().map(provider => provider.slug)).toEqual(['workbuddy'])
  })

  it('keeps an existing connection when a push comes back empty (a transient read is not a wipe)', () => {
    applyDiscoveredQuotaApps([
      frame('workbuddy', { models: ['hy3'] })
    ])

    applyDiscoveredQuotaApps([])

    expect(desktopQuotaProviders().map(provider => provider.slug)).toEqual(['workbuddy'])
  })
})

// The hub decides pickability, and the shell is the only one allowed to read it:
// these are the rules the renderer follows from the pushed frame. Nothing here names
// a model — a leg leaves the picker because the hub said so, and comes back when its
// sign-in is renewed.
describe('hub verdict frames', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('keeps the last good list when the shell reports no answer (an empty frame is not a verdict)', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    applyDiscoveredQuotaApps([frame('workbuddy', { answered: false, models: [] })])

    expect(desktopQuotaProviders()[0]?.models).toEqual(['hy3'])
    expect(quotaSelectionVerdict('workbuddy', 'hy3')).toEqual({ state: 'unanswered' })
  })

  it('believes an answered nothing-is-pickable instead of reviving the seed', () => {
    // the seed knows 'glm-5.2' for workbuddy; a frame that offers nothing must win
    applyDiscoveredQuotaApps([frame('workbuddy', { blocked: { 'glm-5.2': 'no_tools' } })])

    expect(desktopQuotaProviders()).toEqual([])
    expect(quotaSelectionVerdict('workbuddy', 'glm-5.2')).toEqual({ state: 'gone', reason: 'no_tools' })
  })

  it('serves a renewed credential on the next frame with no code or list change', () => {
    applyDiscoveredQuotaApps([
      frame('antigravity', { models: [], blocked: { 'gemini-3.8': 'credential_expired' } })
    ])
    expect(desktopQuotaProviders()).toEqual([])

    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] })])

    expect(desktopQuotaProviders()[0]?.models).toEqual(['gemini-3.8'])
    expect(quotaSelectionVerdict('antigravity', 'gemini-3.8')).toEqual({ state: 'ok' })
    expect(firstSelectableQuotaModel()).toEqual({ model: 'gemini-3.8', provider: 'antigravity' })
  })

  it('refuses to fall back to a model the hub never confirmed', () => {
    // seed-only app: DESKTOP_QUOTA_MODELS has entries for cursor, but no frame answered
    markConnected({ id: 'cursor', baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' })

    expect(desktopQuotaProviders()[0]?.models).toEqual(DESKTOP_QUOTA_MODELS.cursor)
    expect(firstSelectableQuotaModel()).toBeNull()
  })

  it('keeps the reason readable for the settings card after the leg leaves the picker', () => {
    applyDiscoveredQuotaApps([
      frame('antigravity', { models: [], blocked: { 'gemini-3.8': 'credential_expired' } })
    ])

    expect($connectedDesktopApps.get().antigravity?.blocked).toEqual({ 'gemini-3.8': 'credential_expired' })
    // connected still, so Settings can show the software and say why it is idle
    expect($connectedDesktopApps.get().antigravity?.connected).toBe(true)
  })
})
