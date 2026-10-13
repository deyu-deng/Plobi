import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $connectedDesktopApps,
  applyDiscoveredQuotaApps,
  DESKTOP_QUOTA_MODELS,
  desktopQuotaProviders,
  firstSelectableQuotaModel,
  markConnected,
  quotaSelectionVerdict,
  refreshAllConnectedAppModels
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

// The hub decides pickability; these are the rules the renderer follows from that
// verdict. Nothing here names a model — a leg leaves the picker because the gateway
// said so, and comes back when its sign-in is renewed.
describe('live catalog verdicts', () => {
  const hub = (rows: unknown[]) =>
    vi.fn(async () => ({ ok: true, json: async () => ({ data: rows }) }))

  beforeEach(() => {
    $connectedDesktopApps.set({})
    markConnected({
      id: 'workbuddy',
      models: ['glm-5.2'],
      baseUrl: 'http://127.0.0.1:8000/v1',
      apiKey: 'sk-local-dev-key'
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('believes an answered "nothing here is pickable" instead of reviving the seed', async () => {
    vi.stubGlobal(
      'fetch',
      hub([
        {
          id: 'workbuddy/glm-5.2',
          provider: 'workbuddy',
          selectable: false,
          unselectable_reason: 'no_tools'
        }
      ])
    )

    await refreshAllConnectedAppModels()

    expect(desktopQuotaProviders()).toEqual([])
    // the reason travels with it, so a vanished model can be explained on screen
    expect($connectedDesktopApps.get().workbuddy?.blocked).toEqual({ 'glm-5.2': 'no_tools' })
    expect(quotaSelectionVerdict('workbuddy', 'glm-5.2')).toEqual({ state: 'gone', reason: 'no_tools' })
  })

  it('keeps the last good list when the hub does not answer, and calls it unanswered', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('connection refused')
      })
    )

    await refreshAllConnectedAppModels()

    expect(desktopQuotaProviders()[0]?.models).toEqual(['glm-5.2'])
    // "we could not ask" must never read as "it is broken" — no fallback may fire
    expect(quotaSelectionVerdict('workbuddy', 'glm-5.2')).toEqual({ state: 'unanswered' })
  })

  it('serves a renewed credential on the next tick with no code or list change', async () => {
    vi.stubGlobal(
      'fetch',
      hub([{ id: 'workbuddy/glm-5.2', provider: 'workbuddy', selectable: false, unselectable_reason: 'credential_expired' }])
    )
    await refreshAllConnectedAppModels()
    expect(desktopQuotaProviders()).toEqual([])

    vi.stubGlobal('fetch', hub([{ id: 'workbuddy/glm-5.2', provider: 'workbuddy', selectable: true }]))
    await refreshAllConnectedAppModels()

    expect(desktopQuotaProviders()[0]?.models).toEqual(['glm-5.2'])
    expect(quotaSelectionVerdict('workbuddy', 'glm-5.2')).toEqual({ state: 'ok' })
    expect(firstSelectableQuotaModel()).toEqual({ model: 'glm-5.2', provider: 'workbuddy' })
  })

  it('refuses to fall back to a model the hub never confirmed', async () => {
    $connectedDesktopApps.set({})
    // seed-only app: DESKTOP_QUOTA_MODELS has entries for cursor, but no read landed
    markConnected({ id: 'cursor', baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' })

    expect(desktopQuotaProviders()[0]?.models).toEqual(DESKTOP_QUOTA_MODELS.cursor)
    expect(firstSelectableQuotaModel()).toBeNull()
  })

  it('a push frame cannot resurrect legs the hub already dropped', async () => {
    vi.stubGlobal(
      'fetch',
      hub([{ id: 'workbuddy/glm-5.2', provider: 'workbuddy', selectable: false, unselectable_reason: 'no_tools' }])
    )
    await refreshAllConnectedAppModels()

    applyDiscoveredQuotaApps([
      { id: 'workbuddy', models: ['glm-5.2', 'kimi-k3-1'], baseUrl: 'http://127.0.0.1:8000/v1', apiKey: 'sk-local-dev-key' }
    ])

    expect(desktopQuotaProviders()).toEqual([])
  })
})
