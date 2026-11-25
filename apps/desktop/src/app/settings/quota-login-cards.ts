/**
 * The hub's sign-in catalog, translated into cards.
 *
 * Two rulings live here (user, 2026-10-27 / 2026-11-07):
 *
 * 1. **The list is the adapted-app manifest, not the scan result.** Before 11-07 the
 *    hub answered only for what it could already see, so a fresh machine got an
 *    empty panel with no `+` to press and no way out. The hub now answers for every
 *    adapted app, with `installed` / `state` / `reason_code` — the card can therefore
 *    say *what to do next* instead of *why there is nothing here*.
 * 2. **No mechanics on screen.** A card is a status dot, the product's name, one short
 *    line saying what quota it is, and one control: `+` while it is not configured, a
 *    switch afterwards. Why a leg cannot serve a turn right now (expired credential,
 *    a text-only transport, a CLI we have not wired a login for) is a short line
 *    *under* the row (user, 2026-11-04 — long tooltips overlapped the control and
 *    offered no way out; the reason text moved below, and the control stays operable
 *    to turn the leg off). It is never a machine token like `ok`.
 */

/** The five states the hub is allowed to answer with. */
export type SubscriptionState = 'ready' | 'signed_out' | 'not_installed' | 'not_enabled' | 'unavailable'

export interface AuthCatalogRow {
  aggregate: boolean
  id: string
  /** The CLI / desktop app is on this machine. NOT the same claim as "signed in". */
  installed?: boolean
  kind: 'none' | 'local_session' | 'cli_login' | 'oauth'
  /** A credential verdict: signed in, not signed in. Never "installed". */
  live: boolean
  /** Has the user completed a sign-in? `live` may still be false because the node is down. */
  signed_in?: boolean
  signed_in_reason?: string
  /** The plan's own words when there is no click to offer. Tooltip material only. */
  login_reason?: string
  login?: null | { detail?: string; state?: string; url?: string }
  name: string
  reason: string
  // ── First-run guidance (added 2026-11-07) ────────────────────────────────
  /** The one field the control shape reads. */
  state?: SubscriptionState
  /** A code, never a sentence: the renderer translates it. */
  reason_code?: string
  app_kind?: 'api' | 'cli' | 'desktop-app'
  login_method?: string
  /** Official download page — desktop apps (and most CLIs) have one. */
  download?: null | string
  install_kind?: null | string
  install_command?: null | string
  /**
   * Every way into this product the plugin itself reported (browser flow, key
   * prompt, device code …), in their order, under their own labels. Present
   * since 2026-11-25: 22 of them ship lanes, and one lane per vendor is a lie
   * about the other half (qoder-cn reports 4, zcode 5).
   */
  login_methods?: null | { label: string; placeholder?: string; type?: string }[]
  /** The lane the hub will use when the user does not pick one. */
  login_method_default?: null | string
  intro_key?: string
}

export interface LoginCard {
  /** True when the hub routes this software's quota (vs a login-only CLI). */
  aggregate: boolean
  /** A click can start a sign-in, and what it will do is open the browser. */
  connectable: boolean
  /**
   * Plobi can run this leg's sign-in itself — a browser flow (`oauth`) or the CLI's
   * own login command (`cli_login`). A leg whose credential lives in another app's
   * session (`local_session`) cannot be driven from here: the reason line says
   * where to go instead of pretending a click would fix it.
   */
  canLogin: boolean
  /** The hub says the leg is signed in. */
  connected: boolean
  /** Why there is no click / why the switch is off — tooltip only, never body copy. */
  detail: string
  id: string
  /**
   * The hub's key for the one-line subtitle. The view translates it, and an
   * unknown key falls back to a translated sentence built from `name`.
   */
  introKey: string
  name: string
  /** A sign-in is waiting on the user in the browser. */
  pending: boolean
  /**
   * The sign-in page the hub's session handed back, when there is one.
   *
   * It arrives **late**: the click that starts the sign-in is answered at once,
   * while the command that produces the link finishes a good while later (it
   * installs the vendor's CLI first, then asks the plugin). The renderer opens
   * this link when it shows up — that is the whole sign-in for a browser flow.
   */
  loginUrl?: string
  /** The hub's state machine answer, which decides the control's shape. */
  state: SubscriptionState
  /** Machine code for the failure, translated by the renderer. */
  reasonCode: string
  appKind: 'api' | 'cli' | 'desktop-app'
  loginMethod: string
  /** The lanes to offer, default first. One lane ⇒ the card need not ask. */
  methods?: { label: string; placeholder?: string; type?: string }[]
  /** Official page for the "I have installed it" round trip. */
  download: null | string
  installCommand: null | string
  installKind: null | string
}

/**
 * The subtitle key is **the hub's**, not a table of ours (2026-11-20).
 *
 * Two tables used to sit here: a Chinese sentence per app, and a key map that
 * mirrored the hub's `intro_key` id by id. Both were a second source of truth for
 * a list the hub already owns — an app it added had no entry here, so its card
 * showed the bare product name, and the Chinese sentences meant the four locales
 * shared one language.
 *
 * The card now passes the hub's `intro_key` straight through, and the view
 * resolves it (`subs.cardIntros[key]`), falling back to
 * `subs.cardIntroFallback(name)` — a sentence in the reader's own language, for
 * every locale, including apps nobody has written copy for yet.
 */

/**
 * Whether Plobi routes this software today. `unknown` is not a fifth state the user
 * can see: it is the store having nothing to say, so the card falls back to the
 * hub's state.
 */
export type RoutingVerdict = 'off' | 'on' | 'unknown'

/** The one control a row is allowed to show. See `Docs/specs/settings-card-system.md` §2. */
export type CardControl = 'disabled' | 'loader' | 'none' | 'plus' | 'switch'

/** The three dot colours the state machine is allowed to paint. */
export type CardDot = 'error' | 'idle' | 'live'

const CONFIGURED_STATES: SubscriptionState[] = ['ready', 'unavailable']

/** Is this leg set up at all? `unavailable` counts: it *was* configured, it is just dark. */
export function isConfigured(state: SubscriptionState): boolean {
  return CONFIGURED_STATES.includes(state)
}

/**
 * The card grammar, as a decision rather than as markup (rewritten 2026-11-07):
 *
 * - the user's own `off` outranks everything — the switch stays, unchecked, because
 *   only the person who turned it off may turn it back on;
 * - a configured leg shows the switch; if the hub cannot serve it right now the
 *   switch is rendered disabled, so the toggle never promises what the picker
 *   cannot deliver;
 * - not configured (signed out / not installed / not enabled in config) shows `+`.
 *   This is a hard requirement (user 11-07): a `+` is the entry to first-run setup,
 *   and a switch here would look like "configured, just off" — the opposite claim;
 * - a login-only leg the hub cannot wire has nothing to click, so it keeps no control;
 * - the browser step in flight is the one middle state a click can own.
 */
export function cardControl(card: LoginCard, routing: RoutingVerdict, usable = true): CardControl {
  if (routing === 'off') {
    return 'switch'
  }

  if (card.pending) {
    return 'loader'
  }

  if (isConfigured(card.state)) {
    if (routing === 'on' && !usable) {
      return 'disabled'
    }

    if (routing === 'unknown' && !card.aggregate) {
      // Configured but the hub does not route it (a login-only CLI): a switch would
      // claim Plobi can turn something on, and there is nothing to turn. This rule is
      // older than the rename and stays — it is what keeps `qwen-cli` honest.
      return 'none'
    }

    return 'switch'
  }

  // Not configured. A click has to lead somewhere (setup), otherwise no control at all.
  return card.connectable ? 'plus' : 'none'
}

/**
 * The dot's colour, which is the card's whole status vocabulary.
 *
 * `unavailable` on a configured leg is the one red case (user 11-07: "已配置但不可用 →
 * 状态点标红并显示原因徽标"). Everything not configured stays grey — grey is not an
 * error, it is "you have not done this yet".
 */
export function cardDot(card: LoginCard, routing: RoutingVerdict): CardDot {
  if (card.state === 'ready') {
    return routing === 'off' ? 'idle' : 'live'
  }

  if (card.state === 'unavailable') {
    return 'error'
  }

  return 'idle'
}

/**
 * The whole explanation of a row. Since 2026-11-04 it renders as a short line
 * *under* the card (never a tooltip on the control — tooltips were long, overlapped
 * the switch/arrow, and could not be dismissed by an action). `reason` is the
 * store's human line for why the leg cannot serve a turn right now; `error` is what
 * the shell said when a click failed. The control next to it stays operable so the
 * user can act on the message (turn the leg off) instead of just reading it.
 */
export function cardDetail(
  card: LoginCard,
  error?: string,
  reason?: string,
  unselectableReason?: string
): string {
  return [reason, unselectableReason, card.detail, error].filter(Boolean).join(' · ')
}

/**
 * Their lanes, default first, duplicates dropped.
 *
 * The card only asks the user to choose when there is more than one; putting the
 * hub's default at the top means the fallback click and the default lane agree,
 * instead of the screen naming one lane and the request using another.
 */
export function orderedLanes(row: AuthCatalogRow): { label: string; placeholder?: string; type?: string }[] {
  const seen = new Set<string>()
  const lanes: { label: string; placeholder?: string; type?: string }[] = []
  const preferred = row.login_method_default || row.login_method || ''

  for (const lane of row.login_methods ?? []) {
    if (!lane || typeof lane.label !== 'string' || !lane.label || seen.has(lane.label)) {
      continue
    }

    seen.add(lane.label)
    lanes.push(lane)
  }

  if (!preferred || !seen.has(preferred)) {
    return lanes
  }

  const index = lanes.findIndex(lane => lane.label === preferred)

  return index <= 0 ? lanes : [lanes[index], ...lanes.slice(0, index), ...lanes.slice(index + 1)]
}

export function buildLoginCards(rows: readonly AuthCatalogRow[]): LoginCard[] {
  return rows
    .filter(row => row.id)
    .map(row => {
      const pending = row.login?.state === 'pending'
      const wired = row.kind === 'oauth' || row.kind === 'cli_login'
      // `installed` is the scan answer; a login-only leg with no probe is honest about
      // being signed out (`live=false`), which is exactly when the `+` must appear.
      const installed = row.installed !== false
      // Two questions, two fields (measured 2026-10-28 on Antigravity): has he signed in,
      // and can the leg serve right now. `live` also carries reachability — a Settings →
      // VPN node going away used to turn the card back into a `+` and tell him he had not
      // logged in. The state machine now separates the two explicitly.
      const signedIn = row.signed_in ?? row.live

      const state: SubscriptionState =
        row.state ??
        (signedIn && row.live ? 'ready' : !installed ? 'not_installed' : signedIn ? 'unavailable' : 'signed_out')

      const introKey = row.intro_key || row.id

      return {
        aggregate: row.aggregate,
        canLogin: wired,
        connected: signedIn,
        // A `+` is only worth drawing when pressing it can actually start something:
        // a sign-in, or (for an uninstalled CLI) the install step that precedes it.
        // `pending` short-circuits everything (2026-11-07): the browser round trip is
        // already in flight, so a second click would only start a second sign-in.
        connectable: pending
          ? false
          : installed
            ? wired && !signedIn
            : Boolean(row.install_command) || Boolean(row.download),
        detail: [row.login_reason, row.live ? '' : row.reason, row.login?.detail ?? '']
          .filter(Boolean)
          .join(' · '),
        id: row.id,
        introKey,
        loginUrl: row.login?.url || undefined,
        name: row.name,
        pending,
        state,
        reasonCode: row.reason_code ?? (row.live ? 'ok' : 'credential_missing'),
        appKind: row.app_kind ?? 'api',
        loginMethod: row.login_method ?? 'unknown',
        methods: orderedLanes(row),
        download: row.download ?? null,
        installCommand: row.install_command ?? null,
        installKind: row.install_kind ?? null
      }
    })
}
