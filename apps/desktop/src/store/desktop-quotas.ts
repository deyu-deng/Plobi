import { atom } from 'nanostores'

import { isMockProvider } from '@/app/shell/model-provider-label'
import { persistString, storedString } from '@/lib/storage'
import type { ModelOptionProvider, ModelPricing } from '@/types/plobi'

// ---------------------------------------------------------------------------
// Desktop Quotas — connected-app registry (renderer-side, persisted)
//
// The user activates a closed-source desktop app (Antigravity, Workbuddy) on
// their machine; the local aigw hub (the hidden Local Quota Hub) aggregates the
// quota those subscriptions already pay for and serves it as an OpenAI-compatible
// catalog. This store keeps the hub's *verdict per app per model*, and
// `quotaProviders()` turns it into the rows the chat model selector shows.
//
// A pick here is a normal Plobi backend model: the row carries the hub provider's
// own slug and the joined id (`<app>/<model>`), so switching, sending, persisting
// and remembering the turn all run through the one pipeline every other model
// uses. What this store decides is only *which* of the hub's channels may be
// offered: a leg whose credential has expired or that cannot carry tool calls
// leaves the list on its own and comes back when the desktop app renews it —
// no name list is involved anywhere in this file. `DESKTOP_QUOTA_MODELS` is only
// a seed for the window before the first read has ever landed.
// ---------------------------------------------------------------------------

export interface ConnectedDesktopApp {
  id: string
  name: string
  connected: boolean
  /** Model ids the hub currently reports as *selectable* (tools-capable + live credential). */
  models: string[]
  /** aigw OpenAI-compatible base URL, e.g. http://127.0.0.1:8019/v1 */
  baseUrl?: string
  /** aigw api key (sk-...) */
  apiKey?: string
  /**
   * Why each dropped model is dropped (`unselectable_reason` from the hub, e.g.
   * `credential_expired`, `no_tools`). Kept so a model that vanishes from the
   * picker can be explained instead of leaving the user to guess.
   */
  blocked?: Record<string, string>
  /**
   * True when the last catalog read actually reached the hub. Pruning is only
   * allowed on a frame we asked about: an unreachable hub must never be reported
   * as "you have no models", and the checked-in seed below must never be revived
   * to cover for a leg the hub said is dead.
   */
  answered?: boolean
}

const STORE_KEY = 'plobi.desktop.desktop-quotas'

// Served model ids per app (slug prefix stripped — the provider slug carries it).
// These MUST stay in sync with aigw's AntigravityProvider.served_models (minus
// the `antigravity/` slug prefix). The real Antigravity (Google Gemini Code
// Assist) model ids carry suffixes such as `-preview`.
export const DESKTOP_QUOTA_MODELS: Record<string, string[]> = {
  antigravity: [
    'gemini-3-flash',
    'gemini-3-pro-high',
    'gemini-3-pro-low',
    'gemini-3.1-pro-high',
    'gemini-3.1-pro-low',
    'claude-opus-4-6-thinking',
    'claude-opus-4-5-thinking',
    'claude-sonnet-4-6',
  ],
  // Fallback seed only — the real list is fetched live from the aigw gateway's
  // /v1/models (which serves the Workbuddy provider's configured catalog).
  workbuddy: [
    'workbuddy/default',
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'gpt-5',
    'gemini-3-flash',
  ],
  // Fallback seed only — the real list is fetched live from the aigw gateway's
  // /v1/models (which serves the Cursor provider's configured catalog).
  cursor: [
    'gpt-4o',
    'claude-4-sonnet',
    'auto',
  ],
}

export const DESKTOP_QUOTA_NAMES: Record<string, string> = {
  antigravity: 'Antigravity',
  workbuddy: 'Workbuddy',
  cursor: 'Cursor'
}

function readStore(): Record<string, ConnectedDesktopApp> {
  try {
    const raw = storedString(STORE_KEY)

    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw)

    return parsed && typeof parsed === 'object' ? (parsed as Record<string, ConnectedDesktopApp>) : {}
  } catch {
    return {}
  }
}

export const $connectedDesktopApps = atom<Record<string, ConnectedDesktopApp>>(readStore())

export function markConnected(app: {
  id: string
  name?: string
  models?: string[]
  baseUrl?: string
  apiKey?: string
  blocked?: Record<string, string>
  answered?: boolean
}): void {
  const next = { ...$connectedDesktopApps.get() }
  const previous = next[app.id]

  next[app.id] = {
    // A re-registration (the hub pushed a frame, the user clicked connect) is not
    // a new catalog read. Carrying the old verdict forward until one lands keeps a
    // transient frame from blanking the picker.
    id: app.id,
    name: app.name ?? previous?.name ?? DESKTOP_QUOTA_NAMES[app.id] ?? app.id,
    connected: true,
    // Prefer whatever the shell reported in this frame (the hub's live catalog). If
    // the frame carried nothing, keep what we had until the next frame arrives — the
    // catalog is pushed by the main process, never fetched from here.
    models: app.models ?? previous?.models ?? [],
    baseUrl: app.baseUrl ?? previous?.baseUrl,
    apiKey: app.apiKey ?? previous?.apiKey,
    blocked: app.blocked ?? previous?.blocked,
    answered: app.answered ?? previous?.answered
  }

  $connectedDesktopApps.set(next)
  persistString(STORE_KEY, JSON.stringify(next))
}

export function markDisconnected(id: string): void {
  const next = { ...$connectedDesktopApps.get() }

  delete next[id]

  $connectedDesktopApps.set(next)
  persistString(STORE_KEY, JSON.stringify(next))
}

/**
 * Apply the catalog the main process pushed from the live quota hub. This is the
 * "connect once, auto-connect on every launch" path: the hub is the single source
 * (裁定 46), so every app it currently serves becomes connected here — no manual
 * click, and no model list copied into the UI.
 *
 * Additive: it never disconnects an app the hub simply hasn't reported yet this
 * frame, so a transient empty/failed read cannot wipe a good persisted list.
 */
/** One app as the shell pushes it: the hub's own verdict, not a bare name list. */
export interface QuotaAppFrame {
  /** True only for a frame the hub actually replied to (every failure path sends no frame). */
  answered: boolean
  apiKey: string
  baseUrl: string
  /** Dropped model id → the hub's own reason (`credential_expired`, `no_tools`, …). */
  blocked: Record<string, string>
  id: string
  /** Models picking would actually serve — prefix already stripped. */
  models: string[]
}

export function applyDiscoveredQuotaApps(apps: QuotaAppFrame[]): void {
  if (!apps || apps.length === 0) {
    // An empty frame is what the shell sends when the hub did not answer — the read
    // failed, the key was rejected, or the hub is still coming up. It is never a
    // verdict, so the last good list stands.
    return
  }

  for (const app of apps) {
    if (isMockProvider({ slug: app.id })) {
      continue
    }

    const known = $connectedDesktopApps.get()[app.id]

    markConnected({
      id: app.id,
      apiKey: app.apiKey,
      baseUrl: app.baseUrl,
      // The frame carries the verdict now, because only the main process may read the
      // hub (the renderer's own fetch is preflighted and the hub sends no CORS
      // headers). An app whose legs are all unusable still lands here with an empty
      // `models`: it must leave the picker, but Settings has to be able to say why.
      answered: app.answered,
      blocked: app.blocked ?? {},
      models: app.answered ? app.models : (known?.models ?? [])
    })
  }
}

/** True when `slug` is one of the connected desktop apps the hub serves. */
export function isQuotaAppSlug(slug: string): boolean {
  return Boolean($connectedDesktopApps.get()[slug]?.connected)
}

/**
 * The Plobi backend provider that serves every hub-backed model. This is a
 * routing value, never display copy — 裁定 45.4 keeps the word out of renderer
 * string literals — so it is only ever *matched* here, and the value the UI
 * selects is the catalog row's own slug.
 */
const QUOTA_HUB_ROW = /^aigw$/i

export function findQuotaHubRow(providers: readonly Pick<ModelOptionProvider, 'slug'>[]): string | null {
  return providers.find(provider => QUOTA_HUB_ROW.test(provider.slug))?.slug ?? null
}

/** The bare ids the hub still says are usable for one app, as backend model ids. */
function pickableModelIds(app: ConnectedDesktopApp): string[] {
  const models =
    app.models && app.models.length
      ? app.models
      : app.answered === true
        ? []
        : (DESKTOP_QUOTA_MODELS[app.id] ?? [])

  return models.map(model => `${app.id}/${model}`)
}

/**
 * The chat model selector's provider list, with the hub's own catalog row
 * replaced by one row per connected app.
 *
 * Two things happen here and both are the user's ruling, not decoration:
 *
 * 1. **A pick is an ordinary backend model.** The rows keep the hub row's slug
 *    and carry the joined id the catalog already speaks (`<app>/<model>`), so
 *    selecting one goes through `config.set` / `session.create` / `prompt.submit`
 *    like any other model — the transcript, the memory and the tool calls all
 *    come back through the pipeline that persists them.
 * 2. **Only what the hub says is usable survives.** The catalog's hub row lists
 *    every channel it aggregates, including the CLI leg (no tool calls) and any
 *    app whose credential has expired; the verdict from `/v1/models?selectable=1`
 *    is what filters them out, and an id the backend does not offer is dropped
 *    here too, so nothing in this list can fail for a reason we already knew.
 *
 * Without the backend's hub row there is nothing to route the picks through, so
 * the input comes back untouched rather than a list of promises we cannot keep.
 */
export function quotaProviders(providers: readonly ModelOptionProvider[]): ModelOptionProvider[] {
  const hubRow = providers.find(provider => QUOTA_HUB_ROW.test(provider.slug))

  if (!hubRow) {
    return [...providers]
  }

  const offered = new Set(hubRow.models ?? [])
  const groups: ModelOptionProvider[] = []

  for (const app of Object.values($connectedDesktopApps.get())) {
    // A demo channel is not a quota the user owns, so it never reaches the
    // selector. This is the single place that rule lives for hub-backed rows (the
    // backend's own rows are filtered by `humanizeProviders`).
    if (!app.connected || isMockProvider({ slug: app.id })) {
      continue
    }

    const models = pickableModelIds(app).filter(model => offered.has(model))

    if (models.length === 0) {
      continue
    }

    const pricing: Record<string, ModelPricing> = { ...hubRow.pricing }

    // The draw is the user's own subscription quota, not a metered API call.
    for (const model of models) {
      pricing[model] = { free: true } as ModelPricing
    }

    groups.push({
      ...hubRow,
      free_tier: true,
      models,
      name: app.name,
      pricing,
      total_models: models.length
    })
  }

  return [...providers.filter(provider => provider !== hubRow), ...groups]
}


/**
 * Ask the shell to re-read the hub and push a fresh frame — that is the only way to
 * get a current verdict here, because the renderer's own fetch is preflighted and the
 * hub sends no CORS headers (`src/global.d.ts` says so; a renderer-side read was tried
 * first and every app came back `answered=false`). The reply is asynchronous: it lands
 * on the `plobi:quota-apps` channel and `applyDiscoveredQuotaApps` writes it in, so
 * callers do not await anything and the UI updates when the frame does.
 */
export function requestQuotaCatalog(): void {
  const { requestQuotaApps } = window.plobiDesktop ?? {}

  requestQuotaApps?.()
}

/**
 * What the hub says, right now, about the model the composer is sitting on.
 *
 * Four states, and the difference between them is the whole point:
 *   - `not_quota`  not a hub-backed model id, nothing to police here
 *   - `unanswered` the app is connected but no read has landed — we do not know,
 *                  and "we did not ask" must never be acted on as "it is broken"
 *   - `ok`         the hub offers it
 *   - `gone`       the hub answered and does NOT offer it, with the reason it gave
 *
 * A hub-backed selection carries the app as the first segment of the model id
 * (`workbuddy_api/glm-5.3`), which is what makes the verdict readable without
 * knowing the hub provider's slug.
 */
export type QuotaSelectionVerdict =
  | { state: 'not_quota' }
  | { state: 'unanswered' }
  | { state: 'ok' }
  | { reason: string; state: 'gone' }

export function quotaSelectionVerdict(model: string): QuotaSelectionVerdict {
  const app = hubAppOfModel(model)

  if (!app) {
    return { state: 'not_quota' }
  }

  const modelId = model.slice(model.indexOf('/') + 1)

  if (app.answered !== true) {
    return { state: 'unanswered' }
  }

  if (app.models.includes(modelId)) {
    return { state: 'ok' }
  }

  return { state: 'gone', reason: app.blocked?.[modelId] ?? 'not_offered' }
}

/**
 * The connected app a model id belongs to, found by its first segment
 * (`workbuddy_api/glm-5.3` → that app), or null when the id is not one of ours.
 *
 * This is what lets the rest of the UI talk about a hub-backed pick without ever
 * naming the hub: the verdict reads the app's health through it, and a surface that
 * shows the provider shows the software's own name instead (`desktop-quotas` owns
 * the join, so nobody has to re-derive it).
 */
export function hubAppOfModel(model: string): ConnectedDesktopApp | null {
  const separator = model.indexOf('/')

  if (separator <= 0) {
    return null
  }

  const app = $connectedDesktopApps.get()[model.slice(0, separator)]

  return app?.connected ? app : null
}

/**
 * Where to land when the model in use just became unservable: the first model the
 * hub still offers. Deliberately name-free — the gateway's health verdict decides,
 * not a list copied into the UI. When more than one app is live the order is the
 * hub's own, so the *preference* between them belongs to the user's default model
 * setting, not to this function.
 *
 * The answer is in app form (`provider` = the app, `model` without the prefix) and
 * `selectModel` turns it into the backend form, which keeps the join in one place.
 */
export function firstSelectableQuotaModel(): { model: string; provider: string } | null {
  const apps = Object.values($connectedDesktopApps.get())
    .filter((app) => app.connected && app.answered === true && app.models.length > 0)
    .sort((a, b) => a.id.localeCompare(b.id))

  for (const app of apps) {
    const [model] = app.models

    if (model) {
      return { model, provider: app.id }
    }
  }

  return null
}
