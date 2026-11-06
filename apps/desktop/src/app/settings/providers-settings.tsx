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
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { ChevronRight, Loader2, Plus, Terminal, Trash2 } from '@/lib/icons'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import { disconnectOAuthProvider, getGlobalModelOptions, listOAuthProviders } from '@/plobi'
import {
  $connectedDesktopApps,
  markConnected,
  markDisconnected,
  quotaAppVerdict,
  requestQuotaCatalog,
  type ConnectedDesktopApp,
  type QuotaAppVerdict
} from '@/store/desktop-quotas'
import { notify, notifyError } from '@/store/notifications'
import { $desktopOnboarding, startManualProviderOAuth } from '@/store/onboarding'
import type { EnvVarInfo, OAuthProvider } from '@/types/plobi'

import { isKeyVar, ProviderKeyRows } from './credential-key-ui'
import { SettingsCategoryHeading, useEnvCredentials } from './env-credentials'
import { providerGroup, providerMeta, providerPriority } from './helpers'
import { ListRow, LoadingState, SettingsContent, SettingsGroup } from './primitives'
import { buildLoginCards, cardControl, cardDetail, type LoginCard, type RoutingVerdict } from './quota-login-cards'

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
  const ordered = useMemo(() => sortProviders(providers), [providers])

  if (ordered.length === 0) {
    return null
  }

  const select = (p: OAuthProvider) => startManualProviderOAuth(p.id)

  const featured = ordered.find(p => p.id === FEATURED_ID && !p.status?.logged_in) ?? null
  // Dedupe (2026-11-04): OpenCode Zen renders once, as the disabled static key
  // entry on the Keys tab — drop the same-named OAuth row here.
  const rest = (featured ? ordered.filter(p => p.id !== FEATURED_ID) : ordered).filter(p => p.id !== 'opencode_api')
  // Flat list: connected rows first (they are what the page leads with), then
  // the unconnected rows in `sortProviders` order (curated priority, then name).
  const connected = rest.filter(p => p.status?.logged_in)
  const others = rest.filter(p => !p.status?.logged_in)

  return (
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
      action={
        <div className="flex items-center gap-1">
          <Trail className="size-4 text-muted-foreground" />
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
      }
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

// Desktop Quotas — third provider pillar (parallel to Account / API key). One card
// per piece of software on this machine whose subscription quota Plobi can use, and
// the card grammar lives in `Docs/specs/settings-card-system.md`: icon, name, one
// short line, and exactly one control whose *shape* is the state. Nothing here
// explains how the hub works, and nothing here names the software itself — the hub's
// catalog does.
function DesktopQuotasView() {
  const { t } = useI18n()
  const dq = t.settings.providers.desktopQuotas
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

  // Ask the shell to re-read the hub whenever this view opens. The renderer cannot
  // read the hub itself (its preflight fails — the hub sends no CORS headers), so the
  // hub's own pickability verdict arrives on the pushed frame.
  useEffect(() => {
    requestQuotaCatalog()
    readAuthCatalog()
    readHubSelectable()
  }, [readAuthCatalog, readHubSelectable])

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

  // --- Connect handler (Antigravity via the compliant `agy` CLI route) ---
  // The backend does NOT run an OAuth flow. It verifies the user's own `agy`
  // CLI is installed and starts the local gateway. No token is returned, so
  // success is keyed on `res.ok` alone.
  const handleConnect = async (appId: string) => {
    if (!window.plobiDesktop?.plobiGateway?.auth) {return}
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
      } else {
        setConn((prev) => ({ ...prev, [appId]: 'error' }))
        setErrInfo((prev) => ({ ...prev, [appId]: { code: res.code, message: res.error, installHint: res.installHint } }))
      }
    } catch {
      setConn((prev) => ({ ...prev, [appId]: 'error' }))
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
    if (!window.confirm(dq.disconnectConfirm(card.name))) {
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
    notify({ kind: 'success', message: dq.disconnectedToast(card.name), title: card.name })
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

        const verdictView: QuotaAppVerdict =
          !usable && verdict.state === 'ok'
            ? { reason: unselectableReason ?? 'not_offered', state: 'unusable' }
            : verdict

        // The hub's reason belongs on the control that would fix it.
        const reason
          = !usable
            ? unselectableReason
              ?? (verdict.state === 'unusable'
                ? quotaReason[verdict.reason] ?? t.desktop.quotaReason.unknown(verdict.reason)
                : '')
            : ''

        const click = conn[card.id] ?? 'idle'
        const error = click === 'error' ? errInfo[card.id] : undefined
        // A failed click keeps its detail on the control's tooltip (it is what the
        // user just pressed); the why-this-leg-is-dark line lives under the card.
        const errorTitle = error ? `${dq.connectError(card.name)}${error.message ? `：${error.message}` : ''}` : undefined

        return {
          card,
          checked: routing !== 'off',
          control: cardControl(card, routing, usable),
          // The reason for a dark leg is body copy under the card (user 2026-11-04
          // ruling) — see `belowText`; the control tooltip only carries a click
          // failure, which an action (retry / turn off) can actually resolve.
          belowText: reason || unselectableReason || undefined,
          detail: cardDetail(card, errorTitle, undefined, undefined),
          errorTitle,
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
    [desktopCards, conn, dq, errInfo, hubSelectable, quotaApps, quotaReason]
  )

  /** The one control this row is allowed to show, in the shape the state asks for. */
  function renderControl(row: (typeof rows)[number]) {
    const { card, checked, control, errorTitle, isBusy } = row

    if (control === 'switch' || control === 'disabled') {
      // A disabled switch is "on but dark": the reason lives under the card, and
      // the switch stays operable so the user can act on the message — turn the
      // leg off — instead of only reading it (user 2026-11-04). Only turning it
      // back on is gated while the leg is dark (the dropdown cannot offer it).
      return (
        <Switch
          aria-label={dq.useQuota}
          checked={checked}
          disabled={control === 'disabled' && !checked}
          onCheckedChange={value => (value ? handleTurnOn(card) : handleTurnOff(card))}
          title={errorTitle || undefined}
        />
      )
    }

    // A click still in flight is the only moment this control is grey: it is not
    // broken, it is busy. Everything else that is wrong rides on the tooltip.
    if (control === 'plus' || isBusy) {
      if (isBusy) {
        return (
          <Button aria-label={dq.connecting} disabled size="icon-sm" type="button" variant="secondary">
            <Loader2 className="size-4 animate-spin" />
          </Button>
        )
      }

      return (
        <Button
          aria-label={dq.configure}
          onClick={() => handleConnect(card.id)}
          size="icon-sm"
          title={errorTitle || undefined}
          type="button"
          variant="secondary"
        >
          <Plus className="size-4" />
        </Button>
      )
    }

    if (control === 'loader') {
      return (
        <Button aria-label={dq.connecting} disabled size="icon-sm" type="button" variant="secondary">
          <Loader2 className="size-4 animate-spin" />
        </Button>
      )
    }

    return null
  }

  return (
    <SettingsContent>
      {/* 一组一张卡（用户 11-02 拍「外壳 + 设置」）：组名与那句说明在卡**上方**，
          卡内相邻两行之间一条虚线。OAuth 那一屏与 API 密钥那一屏不在此列（见各自注释）。 */}
      <SettingsGroup description={dq.intro} title={dq.title}>

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
      ) : (
        <div className="grid gap-2">
          {rows.map(row => (
            <ListRow
              action={renderControl(row)}
              // Why a leg is dark reads under the card, on its own row — never a
              // tooltip overlapping the switch (user 2026-11-04 ruling).
              below={
                row.belowText ? (
                  <p
                    className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-destructive"
                    role="note"
                  >
                    {row.belowText}
                  </p>
                ) : undefined
              }
              description={row.card.intro}
              key={row.card.id}
              // Unified card template (2026-11-04): status dot + name + the one
              // switch on the right — same shape as the key cards.
              title={
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    className={cn(
                      'size-2 shrink-0 rounded-full',
                      row.verdict.state === 'ok'
                        ? 'bg-primary'
                        : row.verdict.state === 'unusable'
                          ? 'bg-destructive'
                          : 'bg-(--ui-stroke-secondary)'
                    )}
                  />
                  <span className="truncate">{row.card.name}</span>
                </span>
              }
            />
          ))}
        </div>
      )}
      </SettingsGroup>
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
              <div className="grid gap-2">
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
  onViewChange: (view: ProviderView) => void
  view: ProviderView
}
