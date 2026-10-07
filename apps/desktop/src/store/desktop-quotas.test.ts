import { beforeEach, describe, expect, it } from 'vitest'

import {
  $connectedDesktopApps,
  applyDiscoveredQuotaApps,
  DESKTOP_QUOTA_MODELS,
  desktopQuotaProviders,
  markConnected
} from './desktop-quotas'

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
      { id: 'workbuddy', models: ['hy3', 'glm-5.3'], baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' }
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
      { id: 'mock', models: ['echo'], baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' },
      { id: 'workbuddy', models: ['hy3'], baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' }
    ])

    expect(desktopQuotaProviders().map(provider => provider.slug)).toEqual(['workbuddy'])
  })

  it('keeps an existing connection when a push comes back empty (a transient read is not a wipe)', () => {
    applyDiscoveredQuotaApps([
      { id: 'workbuddy', models: ['hy3'], baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' }
    ])

    applyDiscoveredQuotaApps([])

    expect(desktopQuotaProviders().map(provider => provider.slug)).toEqual(['workbuddy'])
  })
})
