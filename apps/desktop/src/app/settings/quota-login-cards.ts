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

const INTRO: Record<string, string> = {
  antigravity: 'Google Antigravity 订阅额度',
  claude_code_cli: 'Claude Code 订阅额度',
  cline_cli: 'Cline 账号池额度',
  codex_cli: 'Codex 订阅额度',
  cursor: 'Cursor 订阅额度',
  'gemini-cli': 'Gemini CLI 免费额度',
  kiro_cli: 'Kiro 订阅额度',
  opencode_cli: 'OpenCode 免费额度',
  'qwen-cli': 'Qwen Code 免费额度',
  workbuddy: 'CodeBuddy 订阅额度',
  workbuddy_api: 'CodeBuddy 订阅额度'
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

      return {
        aggregate: row.aggregate,
        connected: row.live,
        connectable: wired && installed && !row.live && !pending,
        detail: [row.login_reason, row.live ? '' : row.reason, row.login?.detail ?? '']
          .filter(Boolean)
          .join(' · '),
        id: row.id,
        intro: INTRO[row.id] ?? `${row.name} 订阅额度`,
        name: row.name,
        pending
      }
    })
}
