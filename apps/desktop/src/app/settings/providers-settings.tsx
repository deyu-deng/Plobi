import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { runInTerminal } from '@/app/right-sidebar/store'
import {
  FEATURED_ID,
  FeaturedProviderRow,
  ProviderRow,
  providerTitle,
  sortProviders
} from '@/components/onboarding'
import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useI18n } from '@/i18n'
import { ChevronRight, RefreshCw, Terminal } from '@/lib/icons'
import { normalize } from '@/lib/text'
import { disconnectOAuthProvider, getGlobalModelOptions, listOAuthProviders } from '@/plobi'
import {
  $connectedDesktopApps,
  markConnected,
  markDisconnected,
  quotaAppVerdict,
  type QuotaAppVerdict,
  requestQuotaCatalog
} from '@/store/desktop-quotas'
import { notify, notifyError } from '@/store/notifications'
import { $desktopOnboarding, startManualProviderOAuth } from '@/store/onboarding'
import type { EnvVarInfo, OAuthProvider } from '@/types/plobi'

import { ConfigCard } from './config-card'
import { ConnectionControl } from './connection-control'
import { isKeyVar, ProviderKeyRows } from './credential-key-ui'
import { useEnvCredentials } from './env-credentials'
import { providerGroup, providerMeta, providerPriority } from './helpers'
import { ListRow, LoadingState, SettingsContent, SettingsGroup } from './primitives'
import { QuotaAppIcon } from './quota-app-icon'
import {
  buildLoginCards,
  cardControl,
  cardDetail,
  cardDot,
  isConfigured,
  type LoginCard,
  type RoutingVerdict
} from './quota-login-cards'
import { setupPhase, SubscriptionSetupDialog } from './subscription-setup'
import type { SettingsView } from './types'

// The embedded terminal (and thus the "run disconnect command" path) only
// exists in the Electron desktop shell, not the web dashboard.
const canRunInTerminal = () => typeof window !== 'undefined' && Boolean(window.plobiDesktop?.terminal)

// Sub-views surfaced as a sidebar subnav: account sign-in vs raw API keys.
export const PROVIDER_VIEWS = ['accounts', 'keys', 'desktop'] as const

// Which software this screen offers is the hub's answer, never a list in this file:
// adding a leg must not mean editing the app (user 2026-10-26). The screen therefore
// has no names of its own to fall back on while the catalog is coming up — it shows
// the shape of rows without inventing what is in them.
//
// One piece of desktop software can be served by more than one hub leg — CodeBuddy has
// the CLI transport and the subscription HTTP transport, and only the latter can bring
// tool calls back into our loop. The card describes the *software*, so its status is
// the best verdict across its legs: usable if any leg is pickable.
const QUOTA_APP_PEERS: Record<string, string[]> = {
  workbuddy: ['workbuddy', 'workbuddy_api']
}

/** Case-insensitive name match for the three provider tabs' search boxes. */
function matchesQuery(name: string, query: string): boolean {
  const q = query.trim().toLowerCase()

  return q === '' || name.toLowerCase().includes(q)
}

/** Every hub leg that is the same piece of software behind this card. */
function peerLegs(id: string): string[] {
  return QUOTA_APP_PEERS[id] ?? [id]
}


export type ProviderView = (typeof PROVIDER_VIEWS)[number]

// Group the env catalog by provider — one ListRow per vendor plus optional
// advanced overrides (base URL, region, etc.). Groups without a key field are
// skipped.
//
// Grouping key precedence:
//   1. Backend `provider_label` / `provider` (from the unified provider catalog
//      in plobi_cli/provider_catalog.py) — the SAME provider identity
//      `plobi model` uses. This is authoritative: a provider tagged by the
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
      // The overlay's constants layer marks a few legacy legs explicitly off
      // (`OPENCODE_ZEN_`): that flag lives on the PROVIDER_GROUPS entry and rides
      // down through providerMeta — the card consumes it, it is no longer dead.
      disabled: meta?.disabled,
      disabledReason: meta?.disabledReason,
      docsUrl: meta?.docsUrl ?? primary[1].url ?? undefined,
      hasAnySet: entries.some(([, i]) => i.is_set),
      name,
      primary,
      priority: providerPriority(name)
    })
  }

  return groups.sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
}


// Settings-surface replica of the first-run onboarding picker (`Picker` in
// desktop-onboarding-overlay): same recommended card, same provider rows, and
// the same bottom-right "I have an API key" affordance. Since 2026-11-04 the
// list is flat — no "Other providers" disclosure, no collapse button — so
// connected rows lead and every unconnected row is visible in one scroll.
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
  const [query, setQuery] = useState('')
  const ordered = useMemo(() => sortProviders(providers), [providers])
  const matching = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) {
      return ordered
    }
    return ordered.filter(provider => providerTitle(provider).toLowerCase().includes(q))
  }, [ordered, query])

  if (ordered.length === 0) {
    return null
  }

  const select = (p: OAuthProvider) => startManualProviderOAuth(p.id)

  const featured = matching.find(p => p.id === FEATURED_ID && !p.status?.logged_in) ?? null
  // Dedupe (2026-11-04): OpenCode Zen renders once, as the disabled static key
  // entry on the Keys tab — drop the same-named OAuth row here.
  const rest = (featured ? matching.filter(p => p.id !== FEATURED_ID) : matching).filter(p => p.id !== 'opencode_api')
  // Flat list: connected rows first (they are what the page leads with), then
  // the unconnected rows in `sortProviders` order (curated priority, then name).
  const connected = rest.filter(p => p.status?.logged_in)
  const others = rest.filter(p => !p.status?.logged_in)

  return (
    <>
      <SearchField
        aria-label={p.searchKeys}
        onChange={setQuery}
        placeholder={p.searchKeys}
        value={query}
      />
      <SettingsGroup
      action={
        <Button
          className="text-[length:var(--conversation-caption-font-size)]"
          onClick={onWantApiKey}
          size="inline"
          type="button"
          variant="textStrong"
        >
          {p.haveApiKey}
        </Button>
      }
      description={p.intro}
      title={p.connectAccount}
    >
      {featured && <FeaturedProviderRow onSelect={select} provider={featured} />}
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
      {others.map(p => (
        <ProviderRow key={p.id} onSelect={select} provider={p} />
      ))}
      </SettingsGroup>
    </>
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
  // Plobi can clear this provider's creds via the API.
  const canDisconnect = provider.disconnectable ?? provider.flow !== 'external'
  // External (CLI-managed) provider Plobi can't clear via the API, but ships a
  // command we can run in the embedded terminal (Electron shell only).
  const terminalDisconnect = !canDisconnect && Boolean(provider.disconnect_command) && canRunInTerminal()
  // Only fall back to a static "remove it elsewhere" hint when we offer no button.
  const showHint = !canDisconnect && !terminalDisconnect

  return (
    <ListRow
      // One control, same grammar as the other two tabs (user 2026-11-08): signed in =
      // switch (off = disconnect through the existing confirmed path); a provider Plobi
      // cannot clear gets no control — the hint under the row says where to remove it.
      action={(
        <ConnectionControl
          busy={disconnecting}
          checked
          configureLabel={copy.subscriptions.configure}
          mode={showHint ? 'none' : 'toggle'}
          onToggle={(next: boolean) => {
            if (next) {
              return
            }
            if (canDisconnect) {
              onDisconnect(provider)
            } else if (terminalDisconnect) {
              onTerminalDisconnect(provider)
            }
          }}
          toggleLabel={`${copy.disconnect} ${title}`}
        />
      )}
      description={
        <>
          {t.onboarding.flowSubtitles[provider.flow]}
          {showHint && (
            <span className="mt-0.5 block truncate text-[0.68rem] leading-5 text-muted-foreground/70">
              {provider.flow === 'external' ? copy.removeExternalGeneric(title) : copy.removeKeyManaged(title)}
            </span>
          )}
        </>
      }
      onActivate={() => onSelect(provider)}
      title={
        <span className="flex min-w-0 items-center gap-2">
          {/* Connected status dot — same "status dot + name" shape as the key and
              desktop quota cards (2026-11-04 unified template). */}
          <span className="size-2 shrink-0 rounded-full bg-primary" />
          <span className="truncate">{title}</span>
        </span>
      }
    />
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

// Subscriptions — third provider pillar (parallel to Account / API key): one card per
// adapted app whose already-paid quota Plobi can drive. The card grammar lives in
// `Docs/specs/settings-card-system.md`: icon, name, one short line, and exactly one
// control whose *shape* is the state. Nothing here explains how the hub works, and
// nothing here names the software itself — the hub's catalog does.
function SubscriptionsView({ onOpenNetwork }: { onOpenNetwork?: () => void }) {
  const [query, setQuery] = useState('')
  const { t } = useI18n()
  const subs = t.settings.providers.subscriptions
  // The card the setup dialog is walking through, if any. Kept by id: the catalog is
  // re-read every few seconds, so the dialog has to follow the *fresh* row rather than
  // a snapshot taken when `+` was pressed — that is what makes the switch appear on
  // its own once the sign-in lands.
  const [setupId, setSetupId] = useState<null | string>(null)
  // The hub's own verdict per app, pushed by the main process: what is pickable right
  // now, and why the rest is not. Connected is not the same as usable — a sign-in can
  // expire or a channel may not bring tool calls back into our loop, and the card says
  // which, in the hub's words rather than a guess made here.
  const quotaApps = useStore($connectedDesktopApps)

  const quotaReason: Record<string, string> = {
    credential_expired: t.desktop.quotaReason.credential_expired,
    // The hub's own measurement: the upstream refused this id twice, so it sits the
    // box out for a while. A name we cannot serve is a reason to hide it, not to shout.
    model_rejected: t.desktop.quotaReason.model_rejected,
    // Measured: the Free tier refuses HTTP turns, so the leg is not offered at all.
    no_subscription: t.desktop.quotaReason.no_subscription,
    // Settings → VPN owns the node; these two say why a Google leg cannot run.
    no_proxy: t.desktop.quotaReason.no_proxy,
    proxy_unreachable: t.desktop.quotaReason.proxy_unreachable,
    no_desktop_credential: t.desktop.quotaReason.no_desktop_credential,
    no_tools: t.desktop.quotaReason.no_tools,
    not_offered: t.desktop.quotaReason.not_offered
  }

  // The hub's sign-in catalog: which software this machine has, and what a click can
  // do about each. `null` means "the shell has not answered yet" — rows then show as
  // placeholders, never as remembered names.
  const [authCatalog, setAuthCatalog] = useState<null | LoginCard[]>(null)

  // What a click is doing right now, per leg. It is not a status: the hub's catalog
  // decides what the row shows, this only covers the moment a press is in flight.
  type ConnState = 'connecting' | 'error' | 'idle'
  const [conn, setConn] = useState<Record<string, ConnState>>({})
  // Per-app failure detail, surfaced on the control that failed as its tooltip.
  const [errInfo, setErrInfo] = useState<Record<string, { code?: string; message?: string; installHint?: string }>>({})

  // The chat picker's row filter (`quotaProviders` in store/desktop-quotas) drops the
  // whole hub row when the backend catalog marks it `selectable === false` — the
  // switch must not light next to a dropdown that offers nothing. The card mirrors
  // the same catalog row (`QUOTA_HUB_ROW` is store-local; the aggregate slug is
  // `aigw`). `null` means "no hub row in the catalog", which we leave untouched
  // rather than inventing a verdict.
  const [hubSelectable, setHubSelectable] = useState<null | boolean>(null)

  const readHubSelectable = useCallback(() => {
    void getGlobalModelOptions().then(({ providers }) => {
      const hubRow = (providers ?? []).find(p => /^aigw$/i.test(p.slug))
      setHubSelectable(hubRow ? hubRow.selectable !== false : null)
    })
  }, [])

  const readAuthCatalog = useCallback(() => {
    void window.plobiDesktop?.authCatalog?.().then(rows => {
      setAuthCatalog(buildLoginCards(rows))
    })
  }, [])

  // Where the hub expects its config (`~`-redacted). Only the empty state uses it:
  // an empty list with no explanation is the exact screen the user could not act on
  // (2026-11-07), so the one piece of information that turns "nothing here" into
  // "this file is missing" has to be on screen, in the shell's own words.
  const [configPath, setConfigPath] = useState<null | string>(null)
  const readConfigPath = useCallback(() => {
    void window.plobiDesktop?.plobiGateway?.configPath?.().then(res => {
      setConfigPath(res?.configPath ?? null)
    })
  }, [])

  // Ask the shell to re-read the hub whenever this view opens. The renderer cannot
  // read the hub itself (its preflight fails — the hub sends no CORS headers), so the
  // hub's own pickability verdict arrives on the pushed frame.
  useEffect(() => {
    requestQuotaCatalog()
    readAuthCatalog()
    readConfigPath()
    readHubSelectable()
  }, [readAuthCatalog, readConfigPath, readHubSelectable])

  // "Retry" on the empty state: ask the shell again, and go back to the skeleton
  // while it answers so the button cannot look inert. Nothing here starts or
  // restarts the gateway — pressing a button in Settings must not spawn processes.
  const retryAuthCatalog = useCallback(() => {
    setAuthCatalog(null)
    requestQuotaCatalog()
    readAuthCatalog()
    readConfigPath()
    readHubSelectable()
  }, [readAuthCatalog, readConfigPath, readHubSelectable])

  // While a sign-in is waiting on him in the browser, keep asking. Without this the card
  // only updates when the view re-mounts, so a login that finished perfectly kept showing
  // the `+` he had already pressed (his report 2026-10-28: 「登录完了回来还是个加号」).
  const anyPending = Boolean(authCatalog?.some(card => card.pending))
  // Two cadences, and the slow one never stops while this view is open: a spawn-based leg
  // reports its sign-in state only after its CLI answers, which lands *after* boot
  // (measured 2026-10-29: cline ~10 s, qwen ~25 s). Without the slow read, a Settings
  // view opened during those seconds would keep the `+` it read before the probe landed,
  // until he closed and reopened it — the same complaint, one boot later.
  useEffect(() => {
    const timer = setInterval(() => {
      readAuthCatalog()
      requestQuotaCatalog()
    }, anyPending ? 4000 : 15000)

    return () => clearInterval(timer)
  }, [anyPending, readAuthCatalog])

  // --- Sign-in handler, shared with the first-run dialog ---
  // The backend does NOT run the OAuth dance itself: it starts whatever the app's own
  // adapter can start (CLI auth command, browser hop, local session), so success is
  // keyed on `res.ok` alone. It returns that verdict instead of swallowing it, because
  // the setup dialog has to say out loud when a sign-in could not be started.
  const handleConnect = async (appId: string): Promise<{ error?: string; ok: boolean }> => {
    if (!window.plobiDesktop?.plobiGateway?.auth) {
      return { ok: false }
    }
    setConn((prev) => ({ ...prev, [appId]: 'connecting' }))
    setErrInfo((prev) => { const n = { ...prev }; delete n[appId];

 return n })

    try {
      const res = await window.plobiDesktop.plobiGateway.auth(appId)

      if (res.ok) {
        // Persist the connected app so its models surface in the chat selector
        // (see store/desktop-quotas) and the gateway baseUrl for live routing.
        // Workbuddy rides the shared product hub, so it carries its own hub key
        // (`sk-local-dev-key`) — use whatever the hub returned, and only fall
        // back to the per-app key (`sk-local-<appId>`) for the standalone gateways
        // (Antigravity / Cursor) that still mint their own.
        // A login-only leg (Gemini/Qwen as ACP) has no hub models to route, so it must
        // not be written into the routing store: that store decides what the picker
        // offers, and an empty row there is a promise we cannot keep.
        const isRoutedLeg = authCatalog?.find(candidate => candidate.id === appId)?.aggregate !== false

        if (isRoutedLeg) {markConnected({
          id: appId,
          // A manual connect is not a health verdict either: seed whatever the
          // connect call reported, and nothing otherwise — the hub's frame arrives a
          // moment later and is the thing that decides what lands in the picker.
          models: res.models ?? [],
          baseUrl: res.baseUrl,
          apiKey: res.apiKey ?? `sk-local-${appId}`
        })}

        // The click is over; what the row shows from here is the hub's answer, not
        // this local state, so it goes back to idle and the catalog is re-read.
        setConn((prev) => ({ ...prev, [appId]: 'idle' }))
        requestQuotaCatalog()
        readAuthCatalog()

        return { ok: true }
      }

      setConn((prev) => ({ ...prev, [appId]: 'error' }))
      setErrInfo((prev) => ({ ...prev, [appId]: { code: res.code, message: res.error, installHint: res.installHint } }))

      return { error: res.error, ok: false }
    } catch {
      setConn((prev) => ({ ...prev, [appId]: 'error' }))

      return { ok: false }
    }
  }

  // --- The switch ---
  // Connect once and the hub keeps this app wired up on every launch from then
  // on (`WP-AIGW-BOOTSYNC`), which is the whole point of a subscription leg: you log
  // in once over there. Turning it off is therefore not "log out" — Plobi owns no
  // credential for a CodeBuddy/WorkBuddy subscription — it is the user saying
  // *stop routing through this*. So it flips one flag on the store record, and
  // every read path (picker, landing spot, composer fallback) consults it.
  const handleTurnOff = (card: LoginCard) => {
    if (!window.confirm(subs.disconnectConfirm(card.name))) {
      return
    }

    for (const leg of peerLegs(card.id)) {
      markDisconnected(leg)
    }

    // A failed click from before is no longer about this switch, so it goes with it
    // rather than lingering in the tooltip.
    setConn((prev) => ({ ...prev, [card.id]: 'idle' }))
    setErrInfo((prev) => {
      const next = { ...prev }

      delete next[card.id]

      return next
    })
    notify({ kind: 'success', message: subs.disconnectedToast(card.name), title: card.name })
  }

  // Only the person who turned this off may turn it back on, and that needs no
  // dialog: it is the reverse of a choice they just made, not a new consequence.
  const handleTurnOn = (card: LoginCard) => {
    for (const leg of peerLegs(card.id)) {
      // Turning on is a request, not a verdict: the record stays `answered:false`
      // until the hub's next frame lands with real models. The card reads
      // unanswered until then, so the switch never lights before the dropdown
      // can actually offer the leg (user 2026-11-04).
      markConnected({ answered: false, id: leg })
    }

    requestQuotaCatalog()
  }

  // One card per piece of software, and the hub's verdict decides it. The row is the
  // software, its one line, and the one control that can act on it — the control's
  // *shape* is the state (`Docs/specs/settings-card-system.md` §2), so the row never
  // says "connected" or "expired" out loud. Why a leg cannot serve a turn right now
  // rides on that control as its tooltip: it is the reason the switch is still there,
  // not a status line the screen should be shouting.
  // OpenCode Zen shows up once, as the disabled static key entry on the Keys tab
  // (constants.ts OPENCODE_ZEN_, per the 2026-11-04 dedupe ruling); this leg must
  // not render a second card of the same name.
  // The one line under the name. The hub sends an `intro_key`, the four locales own the
  // sentence — a catalog row with no translation still gets a truthful fallback rather
  // than an empty line (renamed semantics 2026-11-07).
  const introOf = (card: LoginCard) => subs.cardIntros[card.introKey] ?? subs.cardIntroFallback(card.name)

  const desktopCards = useMemo(
    () => (authCatalog ?? []).filter(card => card.id !== 'opencode_api'),
    [authCatalog]
  )

  const rows = useMemo(
    () =>
      desktopCards.map(card => {
        const peers = peerLegs(card.id)
        const verdict = quotaAppVerdict(quotaApps, peers)

        // The one record this card stands on. `quotaApps` is keyed per leg id
        // (peerLegs expands aggregates), so a card can hold several records.
        const record = peers.map(id => quotaApps[id]).find(app => Boolean(app))

        // The frame's row-level honesty: a hub row that answered with zero selectable
        // models is exactly "selectable === false" in the picker's words; the backend
        // catalog's `aigw` aggregate carries the same flag for the whole hub.
        const legUsable = Boolean(record?.answered === true && (record.models?.length ?? 0) > 0)
        const hubUsable = hubSelectable !== false

        // `off` is the user's own verdict and outranks the hub's, so a leg the hub is
        // still happily serving does not read as on.
        const routing: RoutingVerdict = verdict.state === 'off' ? 'off' : verdict.state === 'ok' ? 'on' : 'unknown'

        // verdict=ok but the hub row is not selectable: the dropdown cannot offer this
        // leg, so the card must not pretend the switch works. Same downgrade for a
        // record the hub answered with nothing pickable.
        const usable = verdict.state === 'ok' && legUsable && hubUsable

        // Registry reason beats the frame reason: it is the exact line the picker would
        // have used, carried per dropped model in the frame's `blocked` map.
        const unselectableReason = record?.blocked ? Object.values(record.blocked)[0] : undefined

        // 帧里给的是码（`credential_expired` / `model_rejected`），是理由不是句子，
        // 所以它和判决一样要先过 `quotaReason` 那张表再上屏。码本身留在
        // `unselectableReason` 里给状态用，但绝不作为文案念给用户听。
        const unselectableText = unselectableReason
          ? quotaReason[unselectableReason] ?? t.desktop.quotaReason.unknown(unselectableReason)
          : ''

        const verdictView: QuotaAppVerdict =
          !usable && verdict.state === 'ok'
            ? { reason: unselectableReason ?? 'not_offered', state: 'unusable' }
            : verdict

        // The hub's reason belongs on the control that would fix it.
        const reason
          = !usable
            ? unselectableText
              || (verdict.state === 'unusable'
                ? quotaReason[verdict.reason] ?? t.desktop.quotaReason.unknown(verdict.reason)
                : '')
            : ''

        // 已配置但不可用 = 状态点标红 + 原因徽标（用户 11-07 的硬要求）。未配置的行
        // 不念状态：新机器上十五行会一起写"未安装"，那是噪音不是信息。
        const stateReason
          = card.state === 'unavailable' ? subs.reason[card.reasonCode] ?? subs.reason.unknown : ''

        const click = conn[card.id] ?? 'idle'
        const error = click === 'error' ? errInfo[card.id] : undefined
        // A failed click keeps its detail on the control's tooltip (it is what the
        // user just pressed); the why-this-leg-is-dark line lives under the card.
        const errorTitle = error ? `${subs.connectError(card.name)}${error.message ? `：${error.message}` : ''}` : undefined

        const control = cardControl(card, routing, usable)
        // Configured but the hub cannot serve it right now = "dark": red dot beside the
        // control, reason in its tooltip. A leg the user turned off is their own choice,
        // not a fault, so no dot.
        const broken = isConfigured(card.state) && routing !== 'off' && !usable
        // 「报错了也一定能通过操作修复」（用户 11-08 20:1x）：一条配过、此刻却不能
        // 服务的腿（凭据过期／节点不通）不能是死路 —— 那个能重登的抽屉本来只有
        // `+` 能开，而 `+` 只给没配过的腿。复用现成的抽屉与现成的文案，不新增
        // 一句屏幕文案；腿自己的凭据在别的应用里（local_session）时不假装能点。
        const canRepair = broken && card.canLogin && setupPhase(card) === 'login'
        const reasonText = [reason, stateReason].filter(Boolean).join(' · ')
        const mode: 'configure' | 'toggle' | 'pending' | 'none' =
          click === 'connecting' || control === 'loader'
            ? 'pending'
            : control === 'plus'
              ? 'configure'
              : control === 'none'
                ? 'none'
                : 'toggle'

        return {
          card,
          broken,
          canRepair,
          // Checked only when this software can actually serve a turn right now — the
          // user's 2026-11-08 invariant, and the reason `connected` changed meaning.
          checked: usable && routing !== 'off',
          control,
          dot: cardDot(card, routing),
          errorTitle: [reasonText, errorTitle].filter(Boolean).join(' · ') || undefined,
          mode,
          // The reason for a dark leg is body copy under the card (user 2026-11-04
          // ruling) — see `belowText`; the control tooltip only carries a click
          // failure, which an action (retry / turn off) can actually resolve.
          belowText: reason || stateReason || undefined,
          detail: cardDetail(card, errorTitle, undefined, undefined),
          // A click that is still in flight is the only moment a control is disabled.
          isBusy: click === 'connecting',
          usable,
          unselectableReason,
          verdict: verdictView
        }
      })
        // Configured legs lead the list (user 2026-11-04): anything that is on (or
        // at least routed) comes first, then usable-but-unconfigured, then the rest.
        .sort(
          (a, b) =>
            Number(b.checked) - Number(a.checked) ||
            Number(b.usable) - Number(a.usable) ||
            a.card.name.localeCompare(b.card.name)
        ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [desktopCards, conn, subs, errInfo, hubSelectable, quotaApps, quotaReason]
  )

  // The row the dialog walks through, resolved from the fresh catalog on every render:
  // a snapshot taken at click time would never notice the sign-in landing.
  const setupCard = setupId ? rows.find(row => row.card.id === setupId)?.card ?? null : null

  return (
    <SettingsContent>
      {/* 一组一张卡（用户 11-02 拍「外壳 + 设置」）：组名与那句说明在卡**上方**，
          卡内相邻两行之间一条虚线。OAuth 那一屏与 API 密钥那一屏不在此列（见各自注释）。 */}
      <SearchField
        aria-label={t.settings.providers.searchKeys}
        onChange={setQuery}
        placeholder={t.settings.providers.searchKeys}
        value={query}
      />
      <SettingsGroup description={subs.intro} title={subs.title}>

      {authCatalog === null ? (
        // The screen does not guess what is on this machine, so while the shell has
        // not answered it shows the shape of rows without any words in them.
        <div className="grid gap-2">
          {[0, 1, 2].map(index => (
            <ListRow
              description={<Skeleton className="h-3 w-40" />}
              icon={<Skeleton className="size-8 rounded-lg" />}
              key={index}
              title={<Skeleton className="h-4 w-28" />}
            />
          ))}
        </div>
      ) : rows.length === 0 ? (
        // An answered-but-empty catalog is not "nothing to show": it means the hub is
        // not answering, and the screen has to say so and offer the one action that
        // can help (ask again). Before this, `[]` fell into the same grid as a
        // populated catalog and rendered a blank panel with no words at all — the
        // Subscriptions screen on a fresh install (user 2026-11-07).
        <div className="flex flex-col items-start gap-2 rounded-lg border border-(--ui-stroke-secondary) p-4">
          <p className="text-[length:var(--conversation-body-font-size)] font-medium">{subs.emptyTitle}</p>
          <p className="text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] opacity-70">
            {subs.emptyIntro}
          </p>
          {configPath ? (
            <p className="font-mono text-[length:var(--conversation-caption-font-size)] break-all opacity-60">
              {subs.expectedConfigPath(configPath)}
            </p>
          ) : null}
          <Button onClick={retryAuthCatalog} size="sm" type="button" variant="secondary">
            <RefreshCw className="size-3.5" />
            {subs.retry}
          </Button>
        </div>
      ) : (
        <div className="grid gap-2">
          {rows.filter(row => matchesQuery(row.card.name, query)).map(row => (
            // One card grammar for all three provider pillars (user 2026-11-08): the
            // same shell as the Sources screen — icon, name, one line, one control.
            <ConfigCard
              avatar={<QuotaAppIcon id={row.card.id} name={row.card.name} />}
              below={
                row.belowText || row.canRepair ? (
                  <div className="flex flex-wrap items-center gap-2">
                    {row.belowText ? (
                      <p
                        className="text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-destructive"
                        role="note"
                      >
                        {row.belowText}
                      </p>
                    ) : null}
                    {row.canRepair ? (
                      <Button
                        onClick={() => setSetupId(row.card.id)}
                        size="inline"
                        type="button"
                        variant="textStrong"
                      >
                        {subs.setup.loginAction}
                      </Button>
                    ) : null}
                  </div>
                ) : undefined
              }
              busy={row.isBusy}
              checked={row.checked}
              configureLabel={subs.configure.replace('{name}', row.card.name)}
              configureTestId={`quota-configure-${row.card.id}`}
              description={introOf(row.card)}
              error={row.broken && Boolean(row.errorTitle)}
              errorTitle={row.errorTitle}
              key={row.card.id}
              mode={row.mode}
              monogram={row.card.name.trim().slice(0, 1).toUpperCase()}
              name={row.card.name}
              onConfigure={() => setSetupId(row.card.id)}
              onToggle={next => (next ? handleTurnOn(row.card) : handleTurnOff(row.card))}
              testId={`quota-card-${row.card.id}`}
              toggleLabel={subs.useQuota}
            />
          ))}
        </div>
      )}
      </SettingsGroup>

      {/* First-run setup. Mounted only while a card is being walked through, and its
          last act is always a re-read of the catalog — the switch appears because the
          hub said so, never because the dialog wrote it. */}
      {setupCard ? (
        <SubscriptionSetupDialog
          card={setupCard}
          onChanged={() => {
            requestQuotaCatalog()
            readAuthCatalog()
          }}
          onClose={() => setSetupId(null)}
          onLogin={handleConnect}
          onOpenNetwork={onOpenNetwork}
          open
        />
      ) : null}
    </SettingsContent>
  )
}

export function ProvidersSettings({
  onClose,
  onOpenSettingsView,
  onViewChange,
  view
}: ProvidersSettingsProps) {
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
  // Plobi never deletes creds another tool owns behind a silent API call.
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

  // Subscriptions is its own pillar — render it directly, independent of the
  // Account / API-key split below.
  if (view === 'desktop') {
    return <SubscriptionsView onOpenNetwork={() => onOpenSettingsView?.('network')} />
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

    // Configured groups lead (user 2026-11-04), then curated priority, then name.
    const orderedGroups = visibleGroups
      .slice()
      .sort(
        (a, b) =>
          Number(b.hasAnySet) - Number(a.hasAnySet) ||
          a.priority - b.priority ||
          a.name.localeCompare(b.name)
      )

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
            {orderedGroups.length > 0 ? (
              /* 一组一张卡（用户 11-08：「API key 那一页还是没有和其他页的一样变成卡片」）：
                 这一页原来是一堆**裸行**浮在页底上，与模型／外观／通知那些页各挂各的卡
                 长得完全不是一回事。改走 `SettingsGroup` 那件：行住进同一张 `.dl-card`，
                 行与行之间**不画线**（11-07 统一令：卡内相邻两行不画线，靠上下呼吸分组）。
                 卡片不带抬头——这一屏的组名就是侧栏那一条「API 密钥」，再写一遍是重复。 */
              <SettingsGroup>
                {orderedGroups.map(group => (
                  <ProviderKeyRows
                    expanded={openProvider === group.name}
                    group={group}
                    key={group.name}
                    onExpand={() => setOpenProvider(group.name)}
                    onToggle={() => setOpenProvider(prev => (prev === group.name ? null : group.name))}
                    rowProps={rowProps}
                  />
                ))}
              </SettingsGroup>
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
  disabled?: boolean
  disabledReason?: string
  docsUrl?: string
  hasAnySet: boolean
  name: string
  primary: [string, EnvVarInfo]
  priority: number
}

interface ProvidersSettingsProps {
  onClose: () => void
  /**
   * Jump to a sibling settings view. Only the subscription setup dialog uses it, and
   * only for one hop: a network failure is fixed in Proxy, not here.
   */
  onOpenSettingsView?: (view: SettingsView) => void
  onViewChange: (view: ProviderView) => void
  view: ProviderView
}
