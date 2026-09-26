import { atom } from 'nanostores'

import { persistString, storedString } from '@/lib/storage'
import type { ModelOptionProvider, ModelPricing } from '@/types/hermes'

// ---------------------------------------------------------------------------
// Desktop Quotas — connected-app registry (renderer-side, persisted)
//
// When the user activates a closed-source desktop app (Antigravity, Cursor,
// Workbuddy) in Settings → Providers → Desktop Quotas, its served models are
// injected into the chat model selector here. Selecting one sets *local* model
// state and routes the chat through the aigw hub (the hidden Local Hub) — the
// Hermes backend never learns about these providers.
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
    // The canonical model list lives in DESKTOP_QUOTA_MODELS (kept in sync with
    // the aigw adapter's `served_models`). Always prefer it over the snapshot
    // persisted at connect time, so a model-list update ships without forcing
    // the user to disconnect/reconnect. The persisted `app.models` is only a
    // fallback for apps not present in the registry.
    // Prefer the live list we persisted from the gateway (app.models); fall
    // back to the registry constant only if the live fetch never populated it.
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
