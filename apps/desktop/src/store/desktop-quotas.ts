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
// upstream, e.g. Antigravity's `fetchAvailableModels`). The `DESKTOP_QUOTA_MODELS`
// constant below is only a last-resort fallback when the gateway is unreachable.
// ---------------------------------------------------------------------------

export interface ConnectedDesktopApp {
  id: string
  name: string
  connected: boolean
  models: string[]
  /** aigw OpenAI-compatible base URL, e.g. http://127.0.0.1:8019/v1 */
  baseUrl?: string
  /** aigw api key (sk-...) */
  apiKey?: string
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
}): void {
  const next = { ...$connectedDesktopApps.get() }

  next[app.id] = {
    id: app.id,
    name: app.name ?? DESKTOP_QUOTA_NAMES[app.id] ?? app.id,
    connected: true,
    // Prefer whatever the backend reported at connect time (the gateway's live
    // catalog). If the backend passed nothing, store an empty list and let
    // refreshConnectedAppModels() fill it from /v1/models right after connect.
    models: app.models ?? [],
    baseUrl: app.baseUrl,
    apiKey: app.apiKey
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
export function applyDiscoveredQuotaApps(
  apps: Array<{ apiKey: string; baseUrl: string; id: string; models: string[] }>
): void {
  if (!apps || apps.length === 0) {
    return
  }

  for (const app of apps) {
    if (isMockProvider({ slug: app.id })) {
      continue
    }

    markConnected({
      id: app.id,
      models: app.models,
      baseUrl: app.baseUrl,
      apiKey: app.apiKey
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
    const models =
      app.models && app.models.length
        ? app.models
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
 * Fetch the live model catalog for a connected app directly from the aigw
 * gateway's OpenAI-compatible `/v1/models` endpoint — the authoritative source,
 * since the gateway discovers the real list from upstream (e.g. Antigravity's
 * fetchAvailableModels). Returns the model ids with the `<app>/` slug prefix
 * stripped, or null on any failure (so callers can keep the existing list).
 */
async function fetchDesktopQuotaModels(
  baseUrl: string,
  apiKey: string,
  appId: string
): Promise<string[] | null> {
  const url = `${baseUrl.replace(/\/+$/, '')}/models`

  try {
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` }
    })

    if (!res.ok) {
      return null
    }

    const data = (await res.json()) as {
      data?: Array<{ id?: string; provider?: string }>
    }

    const ids = (data.data ?? [])
      .filter((m) => m?.provider === appId && typeof m.id === 'string')
      .map((m) => m.id!.replace(new RegExp(`^${appId}/`), ''))
      .filter(Boolean)

    return ids.length ? ids : null
  } catch {
    return null
  }
}

/**
 * Re-fetch the live model list for one connected app from its aigw gateway and
 * persist it. Never wipes an existing list to empty: if the gateway returns
 * nothing (offline / not ready), we keep what we already have, then fall back
 * to the registry constant only as a last resort.
 */
export async function refreshConnectedAppModels(appId: string): Promise<void> {
  const app = $connectedDesktopApps.get()[appId]

  if (!app?.connected || !app.baseUrl) {
    return
  }

  const live = await fetchDesktopQuotaModels(app.baseUrl, app.apiKey ?? '', appId)

  const next = { ...$connectedDesktopApps.get() }
  const current = next[appId]

  if (!current) {
    return
  }

  const models =
    live && live.length
      ? live
      : current.models && current.models.length
        ? current.models
        : (DESKTOP_QUOTA_MODELS[appId] ?? [])

  next[appId] = { ...current, models }
  $connectedDesktopApps.set(next)
  persistString(STORE_KEY, JSON.stringify(next))
}

/** Refresh the live model list for every currently-connected app. */
export async function refreshAllConnectedAppModels(): Promise<void> {
  const apps = $connectedDesktopApps.get()

  await Promise.all(
    Object.values(apps)
      .filter((a) => a.connected)
      .map((a) => refreshConnectedAppModels(a.id))
  )
}
