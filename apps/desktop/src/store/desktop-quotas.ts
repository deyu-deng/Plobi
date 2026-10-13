import { atom } from 'nanostores'

import { isMockProvider } from '@/app/shell/model-provider-label'
import { persistString, storedString } from '@/lib/storage'
import type { ModelOptionProvider, ModelPricing } from '@/types/plobi'

// ---------------------------------------------------------------------------
// Desktop Quotas — connected-app registry (renderer-side, persisted)
//
// When the user activates a closed-source desktop app (Antigravity, Cursor,
// Workbuddy) in Settings → Providers → Desktop Quotas, its served models are
// injected into the chat model selector here. Selecting one sets *local* model
// state and routes the chat through the aigw hub (the hidden Local Hub) — the
// Plobi backend never learns about these providers.
//
// The model list is no longer hardcoded. It is fetched live from the aigw
// gateway's `/v1/models` endpoint (which itself discovers the real catalog from
// upstream, e.g. Antigravity's `fetchAvailableModels`), and the gateway marks each
// row with whether picking it would actually work. What the hub offers is what the
// selector shows: a leg whose credential has expired or that cannot carry tool
// calls leaves the list on its own and comes back when the desktop app renews it —
// no name list is involved anywhere in this file. `DESKTOP_QUOTA_MODELS` is only a
// seed for the window before the first read has ever landed.
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

/** True when `slug` is a connected desktop-quota app (its models route to aigw). */
export function isDesktopQuotaProvider(slug: string): boolean {
  return Boolean($connectedDesktopApps.get()[slug]?.connected)
}

/** Base URL of the aigw hub for a connected app, if known. */
export function desktopQuotaBaseUrl(slug: string): string | undefined {
  return $connectedDesktopApps.get()[slug]?.baseUrl
}

/**
 * Synthetic ModelOptionProvider rows for connected desktop apps, merged into the
 * chat model selector so their models surface alongside backend providers.
 * Display-only: pricing is marked free (draws on the user's own subscription
 * quota) and selecting sets local state rather than a backend model switch.
 */
export function desktopQuotaProviders(): ModelOptionProvider[] {
  const apps = $connectedDesktopApps.get()
  const out: ModelOptionProvider[] = []

  for (const app of Object.values(apps)) {
    // A demo channel is not a quota the user owns, so it never reaches the
    // selector — from any of its three call sites. This is the single place that
    // rule lives for desktop-quota rows (the backend provider rows are filtered
    // by humanizeProviders(), and the gateway itself already excludes its mock
    // channel when Plobi derives the source list).
    if (isMockProvider({ slug: app.id })) {
      continue
    }

    // The authoritative list is the gateway's live `/v1/models` (it discovers the
    // real catalog upstream), persisted into `app.models` at connect/refresh.
    // DESKTOP_QUOTA_MODELS below is only a last-resort seed for the window before
    // that read has ever succeeded — it is NOT canonical, and a model that only
    // exists there must never be offered as if it were.
    //
    // Once the hub has answered, its word stands even when it says "none": an app
    // whose credential just expired has to leave the picker, and reviving the seed
    // here would put the dead model back on screen under a different name.
    const models =
      app.models && app.models.length
        ? app.models
        : app.answered === true
          ? []
          : (DESKTOP_QUOTA_MODELS[app.id] ?? [])

    if (!app.connected || models.length === 0) {
      continue
    }

    const pricing: Record<string, ModelPricing> = {}

    for (const model of models) {
      pricing[model] = { free: true } as ModelPricing
    }

    out.push({
      slug: app.id,
      name: app.name,
      models,
      total_models: models.length,
      authenticated: true,
      pricing,
      free_tier: true
    })
  }

  return out
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
 *   - `not_quota`  the selection belongs to the Plobi backend, nothing to police here
 *   - `unanswered` the app is connected but no read has landed — we do not know, and
 *                  "we did not ask" must never be acted on as "it is broken"
 *   - `ok`         the hub offers it
 *   - `gone`       the hub answered and does NOT offer it, with the reason it gave
 */
export type QuotaSelectionVerdict =
  | { state: 'not_quota' }
  | { state: 'unanswered' }
  | { state: 'ok' }
  | { reason: string; state: 'gone' }

export function quotaSelectionVerdict(provider: string, model: string): QuotaSelectionVerdict {
  const app = $connectedDesktopApps.get()[provider]

  if (!app?.connected) {
    return { state: 'not_quota' }
  }

  if (app.answered !== true) {
    return { state: 'unanswered' }
  }

  if (app.models.includes(model)) {
    return { state: 'ok' }
  }

  return { state: 'gone', reason: app.blocked?.[model] ?? 'not_offered' }
}

/**
 * Where to land when the model in use just became unservable: the first model the
 * hub still offers. Deliberately name-free — the gateway's health verdict decides,
 * not a list copied into the UI. When more than one app is live the order is the
 * hub's own, so the *preference* between them belongs to the user's default model
 * setting, not to this function.
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
