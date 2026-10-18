import { beforeEach, describe, expect, it } from 'vitest'

import type { ModelOptionProvider } from '@/types/plobi'

import {
  $connectedDesktopApps,
  applyDiscoveredQuotaApps,
  DESKTOP_QUOTA_MODELS,
  findQuotaHubRow,
  firstSelectableQuotaModel,
  hubAppOfModel,
  isQuotaAppSlug,
  markConnected,
  markDisconnected,
  type QuotaAppFrame,
  quotaAppVerdict,
  quotaProviders,
  quotaSelectionBlock,
  quotaSelectionVerdict
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

/**
 * The backend's own catalog, as `model.options` returns it: one row for an
 * ordinary provider and one for the local quota hub, whose model ids already
 * carry the `<app>/` prefix. `quotaProviders` reshapes this list — it is not a
 * renderer-side invention, which is why the tests are written against it.
 */
function catalog(hubModels: string[] = []): ModelOptionProvider[] {
  return [
    { models: ['gpt-4o'], name: 'OpenAI', slug: 'openai', total_models: 1 } as ModelOptionProvider,
    {
      authenticated: true,
      models: hubModels,
      name: 'Local Quota Hub',
      slug: 'aigw',
      total_models: hubModels.length
    } as ModelOptionProvider
  ]
}

const rows = (hubModels: string[]) => quotaProviders(catalog(hubModels)).filter(row => row.slug === 'aigw')

// `quotaProviders` is the single combinator behind all three picker surfaces
// (model-menu-panel, model-picker, chat-runtime), so these are the rules that
// decide what the user can pick. The invariant they exist to hold: **a row is a
// backend model** — the hub's slug, the joined id — and anything the backend does
// not offer, or the hub does not vouch for, is not on screen.
describe('quotaProviders', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('rewrites the hub row into one row per connected app, keyed by the hub provider', () => {
    markConnected({ id: 'workbuddy_api', models: ['glm-5.3', 'hy3'], name: 'Workbuddy' })

    const [row] = rows(['workbuddy_api/glm-5.3', 'workbuddy_api/hy3', 'antigravity/gemini-3-pro'])

    expect(row?.slug).toBe('aigw')
    expect(row?.name).toBe('Workbuddy')
    expect(row?.models).toEqual(['workbuddy_api/glm-5.3', 'workbuddy_api/hy3'])
    expect(row?.total_models).toBe(2)
    // The hub's own 44-channel row is replaced, not stacked next to these.
    expect(rows(['workbuddy_api/glm-5.3', 'workbuddy_api/hy3', 'antigravity/gemini-3-pro'])).toHaveLength(1)
  })

  it('keeps the rest of the catalog untouched', () => {
    markConnected({ id: 'workbuddy_api', models: ['glm-5.3'] })

    expect(quotaProviders(catalog(['workbuddy_api/glm-5.3'])).map(row => row.slug)).toEqual(['openai', 'aigw'])
  })

  it('drops a channel the hub does not vouch for, even though the backend lists it', () => {
    // The CLI leg hands no tool calls back to Plobi and the expired leg is not
    // served — both still sit in the backend's catalog. Only the hub's verdict
    // decides what ends up on screen.
    applyDiscoveredQuotaApps([
      frame('workbuddy_api', { blocked: { 'glm-5.2': 'credential_expired' }, models: ['hy3'] })
    ])

    const [row] = rows(['workbuddy/glm-5.3', 'workbuddy_api/hy3', 'workbuddy_api/glm-5.2'])

    expect(row?.models).toEqual(['workbuddy_api/hy3'])
  })

  it('drops a model the backend does not offer, even when the hub says it is usable', () => {
    markConnected({ id: 'workbuddy_api', models: ['glm-5.3', 'glm-9.9'] })

    expect(rows(['workbuddy_api/glm-5.3'])[0]?.models).toEqual(['workbuddy_api/glm-5.3'])
  })

  it('never offers the gateway demo channel as if it were quota', () => {
    markConnected({ id: 'mock', models: ['echo'] })
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'Workbuddy' })

    expect(rows(['mock/echo', 'workbuddy_api/hy3']).map(row => row.name)).toEqual(['Workbuddy'])
  })

  it('drops a disconnected app even when it has models cached', () => {
    $connectedDesktopApps.set({
      workbuddy: { connected: false, id: 'workbuddy', models: ['glm-5.3'], name: 'Workbuddy' }
    })

    expect(rows(['workbuddy/glm-5.3'])).toEqual([])
  })

  it('marks every surfaced model free, because it rides the app subscription', () => {
    markConnected({ id: 'workbuddy_api', models: ['glm-5.3', 'kimi-k3-1'] })

    const [row] = rows(['workbuddy_api/glm-5.3', 'workbuddy_api/kimi-k3-1'])

    expect(row?.free_tier).toBe(true)
    expect(row?.pricing?.['workbuddy_api/glm-5.3']).toEqual({ free: true } as never)
  })

  it('offers nothing when the backend has no hub row to route through', () => {
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'Workbuddy' })

    const withoutHub = [catalog()[0]]

    expect(quotaProviders(withoutHub)).toEqual(withoutHub)
  })

  it('prefers the live gateway catalog over the checked-in seed', () => {
    markConnected({ id: 'antigravity', models: ['glm-5.9-pro-max'] })

    expect(rows(['antigravity/glm-5.9-pro-max'])[0]?.models).toEqual(['antigravity/glm-5.9-pro-max'])
    // the point of the test: a model only the live gateway knows must be offered,
    // and a seed entry the gateway no longer serves must not shadow it
    expect(DESKTOP_QUOTA_MODELS.antigravity).not.toContain('glm-5.9-pro-max')
  })

  it('a seed id the backend does not actually offer never reaches the picker', () => {
    // `cursor` is a seed-only app with no frame: its ids would surface as
    // `cursor/gpt-4o`, and the catalog says there is no such channel.
    markConnected({ id: 'cursor', models: [] })

    expect(rows(['workbuddy_api/hy3'])).toEqual([])
  })
})

// The hub decides pickability, and the shell is the only one allowed to read it:
// these are the rules the renderer follows from the pushed frame. Nothing here
// names a model — a leg leaves the picker because the hub said so, and comes back
// when its sign-in is renewed.
describe('hub verdict frames', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('keeps the last good list when the shell reports no answer (an empty frame is not a verdict)', () => {
    applyDiscoveredQuotaApps([frame('workbuddy_api', { models: ['hy3'] })])
    applyDiscoveredQuotaApps([frame('workbuddy_api', { answered: false, models: [] })])

    expect(rows(['workbuddy_api/hy3'])[0]?.models).toEqual(['workbuddy_api/hy3'])
    expect(quotaSelectionVerdict('workbuddy_api/hy3')).toEqual({ state: 'unanswered' })
  })

  it('believes an answered nothing-is-pickable instead of reviving the seed', () => {
    applyDiscoveredQuotaApps([frame('workbuddy_api', { blocked: { 'glm-5.2': 'no_tools' } })])

    expect(rows(['workbuddy_api/glm-5.2'])).toEqual([])
    expect(quotaSelectionVerdict('workbuddy_api/glm-5.2')).toEqual({ state: 'gone', reason: 'no_tools' })
  })

  it('serves a renewed credential on the next frame with no code or list change', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: [], blocked: { 'gemini-3.8': 'credential_expired' } })])
    expect(rows(['antigravity/gemini-3.8'])).toEqual([])

    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] })])

    expect(rows(['antigravity/gemini-3.8'])[0]?.models).toEqual(['antigravity/gemini-3.8'])
    expect(quotaSelectionVerdict('antigravity/gemini-3.8')).toEqual({ state: 'ok' })
    expect(firstSelectableQuotaModel()).toEqual({ model: 'gemini-3.8', provider: 'antigravity' })
  })

  it('refuses to fall back to a model the hub never confirmed', () => {
    markConnected({ id: 'cursor', apiKey: 'sk-local-dev-key', baseUrl: 'http://127.0.0.1:8000/v1' })

    expect(firstSelectableQuotaModel()).toBeNull()
  })

  it('keeps the reason readable for the settings card after the leg leaves the picker', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: [], blocked: { 'gemini-3.8': 'credential_expired' } })])

    expect($connectedDesktopApps.get().antigravity?.blocked).toEqual({ 'gemini-3.8': 'credential_expired' })
    // connected still, so Settings can show the software and say why it is idle
    expect($connectedDesktopApps.get().antigravity?.connected).toBe(true)
  })

  it('reads a non-hub model id as not ours to police', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] })])

    expect(quotaSelectionVerdict('gpt-4o')).toEqual({ state: 'not_quota' })
    expect(quotaSelectionVerdict('somebody-elses/gemini-3.8')).toEqual({ state: 'not_quota' })
  })
})

describe('findQuotaHubRow', () => {
  it('returns the backend row that routes hub-backed picks', () => {
    expect(findQuotaHubRow(catalog(['workbuddy_api/hy3']))).toBe('aigw')
    expect(findQuotaHubRow([catalog()[0]])).toBeNull()
  })

  // The composer pill names the software behind a hub-backed pick instead of the
  // hub's own slug (裁定 45.4 keeps that word off the screen), and this is the only
  // lookup it has to do to get there.
  it('reads the owning app off a joined model id', () => {
    applyDiscoveredQuotaApps([frame('workbuddy_api', { models: ['hy3'] })])

    expect(hubAppOfModel('workbuddy_api/hy3')?.id).toBe('workbuddy_api')
    expect(hubAppOfModel('gpt-4o')).toBeNull()
    expect(hubAppOfModel('somebody-elses/hy3')).toBeNull()
  })
})

// The Desktop Quotas card is per *software*, and the card's whole job is to stop
// reporting the click that asked for the catalog instead of the catalog itself.
// These are the four states it can show, and the invariant underneath them: only a
// verdict the hub actually gave may be acted on — "we did not ask" is never
// dressed up as either "it works" or "it is broken".
describe('quotaAppVerdict', () => {
  // CodeBuddy rides two hub legs; the card describes the software, not the leg.
  const CODEBUDDY_LEGS = ['workbuddy', 'workbuddy_api']
  const verdict = () => quotaAppVerdict($connectedDesktopApps.get(), CODEBUDDY_LEGS)

  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('calls the software usable when the hub vouches for any one of its legs', () => {
    applyDiscoveredQuotaApps([
      frame('workbuddy_api', { models: [] }),
      frame('workbuddy', { models: ['hy3', 'glm-5.3'] })
    ])

    expect(verdict()).toEqual({ models: ['hy3', 'glm-5.3'], state: 'ok' })
  })

  it('carries the hub\'s own reason out when it vouches for none of them', () => {
    applyDiscoveredQuotaApps([
      frame('workbuddy', { models: [], blocked: { 'hy3': 'credential_expired' } }),
      frame('workbuddy_api', { models: [] })
    ])

    expect(verdict()).toEqual({ reason: 'credential_expired', state: 'unusable' })
  })

  it('reads a frame that never came back as "we do not know", not as broken', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { answered: false, models: [] })])

    expect(verdict()).toEqual({ state: 'unanswered' })
  })

  it('reads software the hub has never mentioned as absent, not as a verdict', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] })])

    expect(verdict()).toEqual({ state: 'absent' })
  })

  it('reports unusable again once the models stop being served', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { models: [] })])
    expect(verdict()).toEqual({ reason: 'not_offered', state: 'unusable' })

    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    expect(verdict().state).toBe('ok')
  })

  it('answers one app from its own record when no peer is known', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] })])

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['antigravity'])).toEqual({
      models: ['gemini-3.8'],
      state: 'ok'
    })
  })
})

// Disconnect is the one verdict in this store that is not the hub's. The hub
// serves a signed-in desktop app whether we route through it or not, and its
// frames come back every 60 seconds, so "the user said stop" has to be something
// the discovery path is not allowed to overwrite. These are the rules that make
// it stick — and, just as importantly, come back when the user changes their mind.
describe('disconnect (the user\'s own verdict)', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
  })

  it('stays off when the hub keeps reporting the app on every re-read', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    markDisconnected('workbuddy')

    // This is what the 60-second tick looks like. Without the opt-out being
    // honoured here, the button the user just pressed would undo itself.
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy'])).toEqual({ state: 'off' })
    expect(rows(['workbuddy/hy3'])).toEqual([])
    expect(hubAppOfModel('workbuddy/hy3')).toBeNull()
    expect(isQuotaAppSlug('workbuddy')).toBe(false)
  })

  it('takes its models out of the picker and out of the landing spot', () => {
    applyDiscoveredQuotaApps([frame('antigravity', { models: ['gemini-3.8'] }), frame('workbuddy', { models: ['hy3'] })])
    expect(firstSelectableQuotaModel()).toEqual({ model: 'gemini-3.8', provider: 'antigravity' })

    markDisconnected('antigravity')

    // The composer that loses its model must land somewhere the user still wants.
    expect(firstSelectableQuotaModel()).toEqual({ model: 'hy3', provider: 'workbuddy' })
  })

  it('says why in the user\'s words, not one of the hub\'s reasons', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { blocked: { 'hy3': 'credential_expired' }, models: [] })])
    markDisconnected('workbuddy')

    // The hub's `credential_expired` is still on the record and still true of the
    // app itself — but it is not why the model is unavailable, and saying so
    // would blame the app for a switch the user threw.
    expect(quotaSelectionBlock('workbuddy/hy3')).toBe('disconnected')
    expect(quotaSelectionBlock('antigravity/gemini-3.8')).toBeNull()
    expect(quotaSelectionBlock('gpt-4o')).toBeNull()
  })

  it('comes back when the user connects again', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    markDisconnected('workbuddy')
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])

    // The explicit click, not a frame: `markConnected` is the other half of the pair.
    markConnected({ id: 'workbuddy', models: ['hy3'] })

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['workbuddy'])).toEqual({ models: ['hy3'], state: 'ok' })
    expect(rows(['workbuddy/hy3'])[0]?.models).toEqual(['workbuddy/hy3'])
  })

  it('remembers a disconnect for an app the hub has not reported yet', () => {
    // Nothing on record: the intent itself has to be what survives, or the next
    // frame would re-add the app as if the user had never touched it.
    markDisconnected('cursor')

    expect($connectedDesktopApps.get().cursor?.optedOut).toBe(true)

    applyDiscoveredQuotaApps([frame('cursor', { models: ['gpt-4o'] })])

    expect(quotaAppVerdict($connectedDesktopApps.get(), ['cursor'])).toEqual({ state: 'off' })
    expect(rows(['cursor/gpt-4o'])).toEqual([])
  })

  it('keeps the record so the UI can still name the software behind a held model', () => {
    applyDiscoveredQuotaApps([frame('workbuddy', { models: ['hy3'] })])
    markDisconnected('workbuddy')

    // The record is what a surface naming a stale model id reads, so removing it
    // would leave the composer unable to say whose model it is holding.
    expect($connectedDesktopApps.get().workbuddy?.name).toBe('Workbuddy')
    expect($connectedDesktopApps.get().workbuddy?.models).toEqual(['hy3'])
  })
})
