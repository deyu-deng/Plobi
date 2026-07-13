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
// The model list is static per app and mirrors the aigw adapter's
// `served_models`, minus the `<app>/` slug prefix (the provider slug carries it).
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

const STORE_KEY = 'vaelis.desktop.desktop-quotas'

// Served model ids per app (slug prefix stripped — the provider slug carries it).
export const DESKTOP_QUOTA_MODELS: Record<string, string[]> = {
  antigravity: ['gemini-3-pro', 'gemini-3-flash', 'claude-sonnet-4-6']
}

export const DESKTOP_QUOTA_NAMES: Record<string, string> = {
  antigravity: 'Antigravity'
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
    models: app.models ?? DESKTOP_QUOTA_MODELS[app.id] ?? [],
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
    if (!app.connected || app.models.length === 0) {
      continue
    }

    const pricing: Record<string, ModelPricing> = {}

    for (const model of app.models) {
      pricing[model] = { free: true } as ModelPricing
    }

    out.push({
      slug: app.id,
      name: app.name,
      models: app.models,
      total_models: app.models.length,
      authenticated: true,
      pricing,
      free_tier: true
    })
  }

  return out
}
