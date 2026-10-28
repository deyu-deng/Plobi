/**
 * The hub's sign-in catalog, translated into cards.
 *
 * Two rulings live here (user, 2026-10-27):
 *
 * 1. **The card list is the scan result.** Plobi never downloads an app or a CLI, so
 *    the hub only lists what is on this machine, and a row that is not here is absent
 *    rather than "go install it".
 * 2. **No mechanics on screen.** A card is an icon, the product's name, one short line
 *    saying what quota it is, and one control: `+` before it is configured, a switch
 *    afterwards. Why a leg cannot serve a turn right now (expired credential, a
 *    text-only transport, a CLI we have not wired a login for) is a tooltip on that
 *    control — never a paragraph under the row, and never a machine token like `ok`.
 */

export interface AuthCatalogRow {
  aggregate: boolean
  id: string
  /** The CLI is on this machine. NOT the same claim as "already signed in". */
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
}

export interface LoginCard {
  /** True when the hub routes this software's quota (vs a login-only CLI). */
  aggregate: boolean
  /** A click can start a sign-in, and what it will do is open the browser. */
  connectable: boolean
  /** The hub says the leg is signed in. */
  connected: boolean
  /** Why there is no click / why the switch is off — tooltip only, never body copy. */
  detail: string
  id: string
  /** One short line: what quota this is. Product copy, not an explanation. */
  intro: string
  name: string
  /** A sign-in is waiting on the user in the browser. */
  pending: boolean
}

export const QUOTA_CARD_INTROS: Record<string, string> = {
  antigravity: 'Antigravity 订阅额度',
  claude_code_cli: 'Claude Code 订阅额度',
  cline_cli: 'Cline 账号池额度',
  codex_cli: 'Codex 订阅额度',
  cursor: 'Cursor 订阅额度',
  gemini_api: 'Gemini CLI 免费额度',
  kiro_cli: 'Kiro 订阅额度',
  opencode_cli: 'OpenCode 免费额度',
  'qwen-cli': 'Qwen Code 免费额度',
  workbuddy: 'CodeBuddy 订阅额度',
  workbuddy_api: 'CodeBuddy 订阅额度'
}

/**
 * Whether Plobi routes this software today. `unknown` is not a fifth state the user
 * can see: it is the store having nothing to say, so the card falls back to the
 * hub's credential verdict.
 */
export type RoutingVerdict = 'off' | 'on' | 'unknown'

/** The one control a row is allowed to show. See `Docs/specs/settings-card-system.md` §2. */
export type CardControl = 'loader' | 'none' | 'plus' | 'switch'

/**
 * The card grammar, as a decision rather than as markup:
 *
 * - the user's own `off` outranks everything — the switch stays, unchecked, because
 *   only the person who turned it off may turn it back on;
 * - otherwise a routed software shows the switch, and a signed-in login-only CLI
 *   shows nothing at all (Plobi owns no toggle for it, and a grey control would be
 *   a promise that pressing it does something);
 * - a signed-out software that this machine can start a sign-in for shows `+`;
 * - the browser step in flight is the one middle state a click can own.
 *
 * "Unusable right now" is deliberately absent: that is not a shape, it is the
 * tooltip on whichever control is already here (`cardDetail`).
 */
export function cardControl(card: LoginCard, routing: RoutingVerdict): CardControl {
  if (routing === 'off') {
    return 'switch'
  }

  if (routing === 'on') {
    return 'switch'
  }

  if (!card.connected) {
    return card.pending ? 'loader' : card.connectable ? 'plus' : 'none'
  }

  return card.aggregate ? 'switch' : 'none'
}

/**
 * The whole explanation of a row, in the one place it is allowed to live: the
 * control's tooltip. `reason` is the store's human line for why the leg cannot
 * serve a turn right now; `error` is what the shell said when a click failed.
 * Neither is ever body copy — see `Docs/specs/settings-card-system.md` §4.
 */
export function cardDetail(card: LoginCard, error?: string, reason?: string): string {
  return [reason, card.detail, error].filter(Boolean).join(' · ')
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
      // logged in. The `+` answers the first question only.
      const signedIn = row.signed_in ?? row.live

      return {
        aggregate: row.aggregate,
        connected: signedIn,
        connectable: wired && installed && !signedIn && !pending,
        detail: [row.login_reason, row.live ? '' : row.reason, row.login?.detail ?? '']
          .filter(Boolean)
          .join(' · '),
        id: row.id,
        intro: QUOTA_CARD_INTROS[row.id] ?? `${row.name} 订阅额度`,
        name: row.name,
        pending
      }
    })
}
