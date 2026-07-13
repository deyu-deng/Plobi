import { useStore } from '@nanostores/react'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { runInTerminal } from '@/app/right-sidebar/store'
import {
  FEATURED_ID,
  FeaturedProviderRow,
  KeyProviderRow,
  ProviderRow,
  providerTitle,
  sortProviders
} from '@/components/onboarding'
import { Button } from '@/components/ui/button'
import { RowButton } from '@/components/ui/row-button'
import { SearchField } from '@/components/ui/search-field'
import { disconnectOAuthProvider, listOAuthProviders } from '@/hermes'
import { useI18n } from '@/i18n'
import { Check, ChevronDown, ChevronRight, Globe, KeyRound, Loader2, Terminal, Trash2 } from '@/lib/icons'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'
import { $desktopOnboarding, startManualProviderOAuth } from '@/store/onboarding'
import { DESKTOP_QUOTA_MODELS, markConnected } from '@/store/desktop-quotas'
import type { EnvVarInfo, OAuthProvider } from '@/types/hermes'

import { isKeyVar, ProviderKeyRows, CREDENTIAL_CONTROL_CLASS } from './credential-key-ui'
import { SettingsCategoryHeading, useEnvCredentials } from './env-credentials'
import { providerGroup, providerMeta, providerPriority } from './helpers'
import { LoadingState, SettingsContent } from './primitives'

// The embedded terminal (and thus the "run disconnect command" path) only
// exists in the Electron desktop shell, not the web dashboard.
const canRunInTerminal = () => typeof window !== 'undefined' && Boolean(window.hermesDesktop?.terminal)

// Parallel group headers ("Connected", "Other providers") so the expanded list
// reads as its own section instead of bleeding into the connected group.
function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 px-0.5 text-[length:var(--conversation-caption-font-size)] font-medium text-(--ui-text-tertiary)">
      {children}
    </p>
  )
}

// Sub-views surfaced as a sidebar subnav: account sign-in vs raw API keys.
export const PROVIDER_VIEWS = ['accounts', 'keys', 'desktop'] as const

// Catalog of closed-source desktop apps whose subscription quota Vaelis can
// route through (aigw upstream adapters). This is the *presentation* list —
// the backend aigw registry (PROVIDER_CLASSES in aigw/registry.py) is the
// source of truth and should stay in sync with it.
//
// `verified` reflects whether aigw's token-capture adapter for that app has
// been proven end-to-end. Per aigw/registry.py, cursor/antigravity/workbuddy
// all still carry `# VERIFY` markers, so none are wired yet.
interface DesktopQuotaApp {
  id: string
  name: string
  description: string
  verified: boolean
}

const DESKTOP_QUOTA_APPS: DesktopQuotaApp[] = [
  {
    id: 'antigravity',
    name: 'Antigravity',
    description: 'Google agent platform — Gemini 3.x models via your Google account.',
    verified: true   // backend auth flow verified end-to-end (2026-07-13)
  },
  {
    id: 'cursor',
    name: 'Cursor',
    description: 'Cursor agent — routes through your Cursor subscription quota.',
    verified: false  // protobuf reverse-engineering not yet completed
  },
  {
    id: 'workbuddy',
    name: 'Workbuddy',
    description: 'Workbuddy desktop agent — config-driven endpoint passthrough.',
    verified: false  // upstream host / token capture not yet identified
  }
]

export type ProviderView = (typeof PROVIDER_VIEWS)[number]

// Group the env catalog by provider — one ListRow per vendor plus optional
// advanced overrides (base URL, region, etc.). Groups without a key field are
// skipped.
//
// Grouping key precedence:
//   1. Backend `provider_label` / `provider` (from the unified provider catalog
//      in hermes_cli/provider_catalog.py) — the SAME provider identity
//      `hermes model` uses. This is authoritative: a provider tagged by the
//      backend always renders a card, even with no PROVIDER_GROUPS row.
//   2. Desktop prefix match (`providerGroup`) — legacy fallback for provider
//      env vars that predate the backend tagging.
// Only entries that resolve to neither (the "Other" bucket) are skipped.
function buildProviderKeyGroups(vars: Record<string, EnvVarInfo>): ProviderKeyGroup[] {
  const buckets = new Map<string, [string, EnvVarInfo][]>()

  for (const [key, info] of Object.entries(vars)) {
    if (info.category !== 'provider') {
      continue
    }

    // Prefer the backend-supplied provider label/id so the Keys tab groups by
    // the same identity the CLI picker uses; fall back to the prefix guess.
    const name = info.provider_label?.trim() || info.provider?.trim() || providerGroup(key)

    if (name === 'Other') {
      continue
    }

    buckets.set(name, [...(buckets.get(name) ?? []), [key, info]])
  }

  const groups: ProviderKeyGroup[] = []

  for (const [name, entries] of buckets) {
    const primary = entries.find(([k, i]) => !i.advanced && isKeyVar(k, i)) ?? entries.find(([k, i]) => isKeyVar(k, i))

    if (!primary) {
      continue
    }

    // Presentation overlay (priority, blurb, docs) is keyed by the prefix-based
    // group name; when the backend introduced this provider it may have no
    // overlay entry, so fall back to the backend/env metadata for display.
    const meta = providerMeta(name)

    groups.push({
      // Advanced = the provider's non-key knobs (base URL, region, deployment).
      // Skip redundant alias key vars (e.g. ANTHROPIC_TOKEN vs ANTHROPIC_API_KEY)
      // so we never render a second "Paste key" input — unless one is already
      // set, in which case keep it visible so it stays clearable.
      advanced: entries
        .filter(([k, i]) => k !== primary[0] && (!isKeyVar(k, i) || i.is_set))
        .sort(([a], [b]) => a.localeCompare(b)),
      description: meta?.description ?? primary[1].description,
      docsUrl: meta?.docsUrl ?? primary[1].url ?? undefined,
      hasAnySet: entries.some(([, i]) => i.is_set),
      name,
      primary,
      priority: providerPriority(name)
    })
  }

  return groups.sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
}


// Deliberately a near-1:1 replica of the first-run onboarding picker
// (`Picker` in desktop-onboarding-overlay): same recommended card, same
// provider rows, same "Other providers" disclosure, same OpenRouter quick-key
// row, and the same bottom-right "I have an API key" affordance. The leaf cards
// are the exact shared components, so the two surfaces stay visually identical.
// Selecting a provider hands off to the shared onboarding overlay, which runs
// that provider's real sign-in flow; the key affordances open the API-key
// catalog below.
function OAuthPicker({
  disconnecting,
  onDisconnect,
  onTerminalDisconnect,
  onWantApiKey,
  providers
}: {
  disconnecting: null | string
  onDisconnect: (provider: OAuthProvider) => void
  onTerminalDisconnect: (provider: OAuthProvider) => void
  onWantApiKey: () => void
  providers: OAuthProvider[]
}) {
  const { t } = useI18n()
  const p = t.settings.providers
  const [showAll, setShowAll] = useState(false)
  const ordered = useMemo(() => sortProviders(providers), [providers])

  if (ordered.length === 0) {
    return null
  }

  const select = (p: OAuthProvider) => startManualProviderOAuth(p.id)

  const featured = ordered.find(p => p.id === FEATURED_ID && !p.status?.logged_in) ?? null
  const rest = featured ? ordered.filter(p => p.id !== FEATURED_ID) : ordered
  // Keep connected accounts grouped and always visible; only the unconnected
  // providers hide behind the disclosure, so the page leads with what's set up.
  // Both lists preserve `sortProviders` order (curated priority, then name).
  const connected = rest.filter(p => p.status?.logged_in)
  const others = rest.filter(p => !p.status?.logged_in)
  const collapsible = others.length > 0
  const showOthers = !collapsible || showAll

  return (
    <section className="mb-5 grid gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <SettingsCategoryHeading icon={KeyRound} title={p.connectAccount} />
        <Button
          className="text-[length:var(--conversation-caption-font-size)]"
          onClick={onWantApiKey}
          size="inline"
          type="button"
          variant="textStrong"
        >
          {p.haveApiKey}
        </Button>
      </div>
      <p className="-mt-2 mb-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
        {p.intro}
      </p>
      {featured && <FeaturedProviderRow onSelect={select} provider={featured} />}
      {connected.length > 0 && (
        <>
          <GroupLabel>{p.connected}</GroupLabel>
          {connected.map(p => (
            <ConnectedProviderRow
              disconnecting={disconnecting === p.id}
              key={p.id}
              onDisconnect={onDisconnect}
              onSelect={select}
              onTerminalDisconnect={onTerminalDisconnect}
              provider={p}
            />
          ))}
        </>
      )}
      {showOthers && (
        <>
          {connected.length > 0 && <GroupLabel>{p.otherProviders}</GroupLabel>}
          {others.map(p => (
            <ProviderRow key={p.id} onSelect={select} provider={p} />
          ))}
          <KeyProviderRow onClick={onWantApiKey} />
        </>
      )}
      {collapsible && (
        <Button
          className="py-1 text-[length:var(--conversation-caption-font-size)]"
          onClick={() => setShowAll(v => !v)}
          size="inline"
          type="button"
          variant="text"
        >
          {showAll ? p.collapse : connected.length > 0 ? p.connectAnother : p.otherProviders}
          <ChevronDown className={cn('size-3.5 transition', showAll && 'rotate-180')} />
        </Button>
      )}
    </section>
  )
}

function ConnectedProviderRow({
  disconnecting,
  onDisconnect,
  onSelect,
  onTerminalDisconnect,
  provider
}: {
  disconnecting: boolean
  onDisconnect: (provider: OAuthProvider) => void
  onSelect: (provider: OAuthProvider) => void
  onTerminalDisconnect: (provider: OAuthProvider) => void
  provider: OAuthProvider
}) {
  const { t } = useI18n()
  const copy = t.settings.providers
  const title = providerTitle(provider)
  const Trail = provider.flow === 'external' ? Terminal : ChevronRight
  // Vaelis can clear this provider's creds via the API.
  const canDisconnect = provider.disconnectable ?? provider.flow !== 'external'
  // External (CLI-managed) provider Vaelis can't clear via the API, but ships a
  // command we can run in the embedded terminal (Electron shell only).
  const terminalDisconnect = !canDisconnect && Boolean(provider.disconnect_command) && canRunInTerminal()
  // Only fall back to a static "remove it elsewhere" hint when we offer no button.
  const showHint = !canDisconnect && !terminalDisconnect

  return (
    <div className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-1 rounded-[6px] transition-colors hover:bg-(--ui-control-hover-background)">
      <RowButton className="min-w-0 px-3 py-2.5 text-left" onClick={() => onSelect(provider)}>
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[length:var(--conversation-text-font-size)] font-semibold">{title}</span>
          <span className="inline-flex shrink-0 items-center gap-1 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            <Check className="size-3" />
            {copy.connected}
          </span>
        </div>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{t.onboarding.flowSubtitles[provider.flow]}</p>
        {showHint && (
          <p className="mt-0.5 truncate text-[0.68rem] leading-5 text-muted-foreground/70">
            {provider.flow === 'external' ? copy.removeExternalGeneric(title) : copy.removeKeyManaged(title)}
          </p>
        )}
      </RowButton>
      <div className="flex items-center gap-1 pr-2">
        <Trail className="size-4 text-muted-foreground transition group-hover:text-foreground" />
        {canDisconnect && (
          <Button
            aria-label={`${t.common.remove} ${title}`}
            disabled={disconnecting}
            onClick={() => onDisconnect(provider)}
            size="icon-xs"
            title={`${t.common.remove} ${title}`}
            type="button"
            variant="ghost"
          >
            {disconnecting ? <Loader2 className="size-3 animate-spin" /> : <Trash2 className="size-3" />}
          </Button>
        )}
        {terminalDisconnect && (
          <Button
            aria-label={`${copy.disconnect} ${title}`}
            onClick={() => onTerminalDisconnect(provider)}
            size="icon-xs"
            title={copy.disconnectInTerminal}
            type="button"
            variant="ghost"
          >
            <Trash2 className="size-3" />
          </Button>
        )}
      </div>
    </div>
  )
}

function NoProviderKeys() {
  const { t } = useI18n()

  return (
    <div className="grid min-h-32 place-items-center px-4 py-8 text-center text-[length:var(--conversation-caption-font-size)] text-muted-foreground">
      {t.settings.providers.noProviderKeys}
    </div>
  )
}

// Desktop Quotas — third provider pillar (parallel to Account / API key).
// Lists the closed-source desktop apps whose subscription quota Vaelis can
// route through. The Local Hub (aigw aggregator) is auto-managed by the
// backend once an app is connected, so it is intentionally not surfaced here.
// Frontend stays thin: it triggers backend OAuth and shows status; all token
// capture and routing lives in the backend.
function DesktopQuotasView() {
  const { t } = useI18n()
  const dq = t.settings.providers.desktopQuotas

  // Per-app connection state: idle | connecting | connected | error
  type ConnState = 'idle' | 'connecting' | 'connected' | 'error'
  const [conn, setConn] = useState<Record<string, ConnState>>({})

  // --- OAuth connect handler (Antigravity only, verified) ---
  const handleConnect = async (appId: string) => {
    if (!window.hermesDesktop?.vaelisGateway?.auth) return
    setConn((prev) => ({ ...prev, [appId]: 'connecting' }))
    try {
      const res = await window.hermesDesktop.vaelisGateway.auth(appId)
      if (res.ok && res.token) {
        // Persist the connected app so its models surface in the chat selector
        // (see store/desktop-quotas) and the gateway baseUrl for live routing.
        markConnected({
          id: appId,
          models: res.models ?? DESKTOP_QUOTA_MODELS[appId],
          baseUrl: res.baseUrl,
          apiKey: 'sk-local-antigravity'
        })
        setConn((prev) => ({ ...prev, [appId]: 'connected' }))
      } else {
        setConn((prev) => ({ ...prev, [appId]: 'error' }))
      }
    } catch {
      setConn((prev) => ({ ...prev, [appId]: 'error' }))
    }
  }

  // Whether any app has been attempted
  const hasAttempted = Object.values(conn).some(s => s !== 'idle')
  const anyConnected = Object.values(conn).some(s => s === 'connected')

  return (
    <SettingsContent>
      <div className="grid gap-4">
        <div className="grid gap-1">
          <SettingsCategoryHeading icon={Globe} title={dq.title} />
          <p className="-mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
            {dq.intro}
          </p>
        </div>

        {/* Status banner — only show "not verified" if nothing has been attempted */}
        {!hasAttempted && !anyConnected && (
        <div className="flex items-start gap-2 rounded-[6px] border border-(--ui-stroke-secondary) bg-(--ui-bg-quaternary) p-3">
          <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
            {dq.notVerified}
          </p>
        </div>
        )}

        {anyConnected && (
        <div className="flex items-start gap-2 rounded-[6px] border border-green-500/30 bg-green-500/5 p-3">
          <Check className="mt-0.5 size-4 shrink-0 text-green-600" />
          <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-green-700 dark:text-green-400">
            App connected — its models are now available in the chat.
          </p>
        </div>
        )}

        <div className="grid gap-2">
          {DESKTOP_QUOTA_APPS.map(app => {
            const state = conn[app.id] || 'idle'
            const isConnected = state === 'connected'
            const isConnecting = state === 'connecting'
            const hasError = state === 'error'
            const canConnect = app.verified && !isConnected && !isConnecting

            return (
            <div
              key={app.id}
              className={cn(
                '@container group/card rounded-[6px] p-3 transition-colors',
                'row-hover',
                'ring-1 ring-(--ui-stroke-secondary)',
                isConnected && 'ring-green-500/30 bg-green-500/[0.02]',
              )}
            >
              <div className="grid grid-cols-1 items-start gap-x-3 gap-y-1.5 @2xl:grid-cols-[minmax(0,1fr)_auto] @2xl:gap-y-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="min-w-0 truncate text-[length:var(--conversation-text-font-size)] font-medium text-foreground">
                    {app.name}
                  </span>
                  <span className={cn(
                    'inline-flex shrink-0 items-center gap-1 px-2 py-0.5 text-xs font-medium',
                    isConnected ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                      : hasError ? 'bg-red-500/15 text-red-700 dark:text-red-400'
                      : app.verified ? 'bg-(--ui-bg-quaternary) text-muted-foreground'
                      : 'bg-(--ui-bg-quaternary) text-muted-foreground'
                  )}>
                    {isConnected ? (
                      <><Check className="size-3" />已连接</>
                    ) : hasError ? (
                      (typeof dq.connectError === 'function' ? dq.connectError(app.name) : dq.connectError) || '连接失败'
                    ) : app.verified ? (
                      dq.notConnected || '未连接'
                    ) : (
                      dq.notVerified
                    )}
                  </span>
                </div>

                <div className="flex min-w-0 items-center gap-2">
                  <Button
                    className={cn(CREDENTIAL_CONTROL_CLASS, 'gap-1.5')}
                    disabled={!canConnect}
                    size="sm"
                    title={
                      isConnecting ? '正在打开浏览器登录…'
                        : isConnected ? dq.connected
                        : !app.verified ? dq.notVerified
                        : dq.connect
                    }
                    variant={isConnected ? 'default' : 'secondary'}
                    onClick={() => canConnect && handleConnect(app.id)}
                  >
                    {isConnecting && (
                      <Loader2 className="size-3.5 animate-spin" />
                    )}
                    {isConnected ? '已连接' : isConnecting ? '登录中…' : dq.connect}
                  </Button>
                </div>

                <div className="grid gap-3 @2xl:col-span-2">
                  <p className="text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
                    {app.description}
                  </p>
                  {hasError && (
                    <p className="text-xs text-red-600 dark:text-red-400">
                      登录失败或被取消。请重试，或检查终端日志。
                    </p>
                  )}
                  {isConnecting && (
                    <p className="flex items-center gap-1.5 text-xs text-(--ui-text-secondary)">
                      <Loader2 className="size-3 animate-spin" />
                      浏览器已打开 — 请用 Google 账号登录并授权。
                    </p>
                  )}
                </div>
              </div>
            </div>
          )})}
        </div>
      </div>
    </SettingsContent>
  )
}

export function ProvidersSettings({ onClose, onViewChange, view }: ProvidersSettingsProps) {
  const { t } = useI18n()
  const { rowProps, vars } = useEnvCredentials()
  const [oauthProviders, setOauthProviders] = useState<OAuthProvider[]>([])
  const [openProvider, setOpenProvider] = useState<null | string>(null)
  const [disconnecting, setDisconnecting] = useState<null | string>(null)
  // Free-text filter for the API-keys view (provider name / env-var key / desc).
  const [keyQuery, setKeyQuery] = useState('')
  // The onboarding overlay owns the OAuth flow. Watch its `manual` flag so we
  // re-read connection state when the user finishes (or dismisses) a sign-in
  // they launched from this page — otherwise the cards keep their stale status.
  const onboardingActive = useStore($desktopOnboarding).manual

  const refreshOAuthProviders = useCallback(async () => {
    // OAuth providers are best-effort — a failure here just hides the panel.
    const { providers } = await listOAuthProviders()
    setOauthProviders(providers)
  }, [])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      if (onboardingActive) {
        return
      }

      try {
        const { providers } = await listOAuthProviders()

        if (!cancelled) {
          setOauthProviders(providers)
        }
      } catch {
        // Ignore — the OAuth panel just won't render.
      }
    })()

    return () => void (cancelled = true)
  }, [onboardingActive])

  // External (CLI-managed) providers can't be cleared via the API by design —
  // Vaelis never deletes creds another tool owns behind a silent API call.
  // Instead we run the documented removal command in the embedded terminal so
  // the user sees exactly what executes, then return them to chat to watch it.
  function handleTerminalDisconnect(provider: OAuthProvider) {
    const command = provider.disconnect_command

    if (!command) {
      return
    }

    const name = providerTitle(provider)

    if (!window.confirm(t.settings.providers.removeTerminalConfirm(name, command))) {
      return
    }

    // Leave the settings overlay so the terminal pane (chat-only) is visible.
    onClose()
    runInTerminal(command)
    notify({
      kind: 'info',
      title: t.settings.providers.removedTitle,
      message: t.settings.providers.removeTerminalRunning(name)
    })
  }

  async function handleDisconnect(provider: OAuthProvider) {
    const name = providerTitle(provider)

    if (!window.confirm(t.settings.providers.removeConfirm(name))) {
      return
    }

    setDisconnecting(provider.id)

    try {
      await disconnectOAuthProvider(provider.id)
      notify({
        durationMs: 3_000,
        kind: 'success',
        title: t.settings.providers.removedTitle,
        message: t.settings.providers.removedMessage(name)
      })
      await refreshOAuthProviders().catch(() => undefined)
    } catch (err) {
      notifyError(err, t.settings.providers.failedRemove(name))
    } finally {
      setDisconnecting(null)
    }
  }

  if (!vars) {
    return <LoadingState label={t.settings.providers.loading} />
  }

  const hasOauth = oauthProviders.length > 0
  // The sidebar subnav owns the Accounts/API-keys split now; with no OAuth
  // providers there's nothing for the "Accounts" view to show, so fall to keys.
  const showApiKeys = view === 'keys' || !hasOauth

  // Desktop Quotas is its own pillar — render it directly, independent of the
  // Account / API-key split below.
  if (view === 'desktop') {
    return <DesktopQuotasView />
  }

  const keyGroups = buildProviderKeyGroups(vars)

  if (showApiKeys) {
    const q = normalize(keyQuery)

    const visibleGroups = q
      ? keyGroups.filter(group => {
          const haystack = [group.name, group.description ?? '', group.primary[0], ...group.advanced.map(([k]) => k)]

          return haystack.some(s => s.toLowerCase().includes(q))
        })
      : keyGroups

    return (
      <SettingsContent>
        {keyGroups.length > 0 ? (
          <div className="grid gap-3">
            <SearchField
              aria-label={t.settings.providers.searchKeys}
              containerClassName="w-full"
              onChange={setKeyQuery}
              placeholder={t.settings.providers.searchKeys}
              value={keyQuery}
            />
            {visibleGroups.length > 0 ? (
              <div className="grid gap-2">
                {visibleGroups.map(group => (
                  <ProviderKeyRows
                    expanded={openProvider === group.name}
                    group={group}
                    key={group.name}
                    onExpand={() => setOpenProvider(group.name)}
                    onToggle={() => setOpenProvider(prev => (prev === group.name ? null : group.name))}
                    rowProps={rowProps}
                  />
                ))}
              </div>
            ) : (
              <div className="grid min-h-24 place-items-center px-4 py-6 text-center text-[length:var(--conversation-caption-font-size)] text-muted-foreground">
                {t.settings.providers.noKeysMatch}
              </div>
            )}
          </div>
        ) : (
          <NoProviderKeys />
        )}
      </SettingsContent>
    )
  }

  return (
    <SettingsContent>
      <OAuthPicker
        disconnecting={disconnecting}
        onDisconnect={provider => void handleDisconnect(provider)}
        onTerminalDisconnect={handleTerminalDisconnect}
        onWantApiKey={() => onViewChange('keys')}
        providers={oauthProviders}
      />
    </SettingsContent>
  )
}

interface ProviderKeyGroup {
  advanced: [string, EnvVarInfo][]
  description?: string
  docsUrl?: string
  hasAnySet: boolean
  name: string
  primary: [string, EnvVarInfo]
  priority: number
}

interface ProvidersSettingsProps {
  onClose: () => void
  onViewChange: (view: ProviderView) => void
  view: ProviderView
}
