/**
 * One row of `GET /plobi/auth` — the hub's own answer to "can a click sign this in,
 * and how". Kept structurally identical to what the preload hands over so this file
 * never has to translate.
 */
export interface AuthCatalogRow {
  aggregate: boolean
  id: string
  kind: 'none' | 'local_session' | 'cli_login' | 'oauth'
  /** The CLI is on this machine. NOT the same claim as "already signed in". */
  installed?: boolean
  live: boolean
  /** The plan's own words when there is no click to offer ("we did not wire it"). */
  login_reason?: string
  login?: null | { detail?: string; state?: string; url?: string }
  name: string
  reason: string
}

/** What one card shows. Plain language only: no "aigw", no provider vocabulary. */
export interface LoginCard {
  /** True when the hub serves this software's quota and the picker can route to it. */
  aggregate: boolean
  /** One line under the name. Says what state it is in and what a click would do. */
  description: string
  id: string
  name: string
  /** A click can start a sign-in (and must open the browser, never a terminal). */
  connectable: boolean
  /** The hub says the leg is usable right now. */
  connected: boolean
  /** A sign-in is already waiting on the user in the browser. */
  pending: boolean
}

const MISSING_CLI = /没在这台机器上找到/

/**
 * Turn the hub's catalog into the cards the settings surface renders.
 *
 * The rules the user asked for (2026-10-26): **one card per piece of software**, one
 * sentence of state, and one control — and nothing that asks him to type. So:
 *
 * - `local_session` is NOT a thing to click: CodeBuddy and Cursor read the desktop
 *   app's own login, and a 连接 button there would be a lie about where the
 *   credential lives.
 * - `none` is "we have not wired this", which is not "it is broken" — it keeps the
 *   plan's own words (`login_reason`, never the credential verdict: printing "ok"
 *   under a card is a machine token, not an answer) and shows no button.
 * - `installed` is not `live`. A CLI on PATH that nobody has signed into must still
 *   offer the click; treating "installed" as "connected" hid the only way in.
 * - a CLI that is not installed is not a sign-in problem either; the reason already
 *   says the CLI is missing, so no button.
 * - `oauth` / `cli_login` while the leg is dead IS the button, and its label says the
 *   browser is what happens next.
 */
export function buildLoginCards(rows: readonly AuthCatalogRow[]): LoginCard[] {
  return rows
    .filter(row => row.id)
    .map(row => {
      const pending = row.login?.state === 'pending'
      const missingCli =
        row.installed === false ||
        MISSING_CLI.test(row.reason) ||
        MISSING_CLI.test(row.login?.detail ?? '')
      const wired = row.kind === 'oauth' || row.kind === 'cli_login'

      let description: string

      if (pending) {
        description = '浏览器里那一步等你点完，回来这里就会变成已连接'
      } else if (row.kind === 'local_session') {
        description = row.login_reason || '用的是这个软件自己的登录，Plobi 这边不用再登一次'
      } else if (row.kind === 'none') {
        description = row.login_reason || row.reason || '这一条还没接上点一下就能登录的路'
      } else if (row.live) {
        description = '已连接'
      } else if (missingCli) {
        description = row.reason
      } else if (row.kind === 'oauth') {
        description = '登录过期了，点连接会跳到浏览器'
      } else {
        // Both halves matter: where it stands, and what the button will do.
        description = row.reason
          ? `${row.reason}，点连接会跳到浏览器`
          : '点连接会跳到浏览器登录，回来就能选它的模型'
      }

      return {
        aggregate: row.aggregate,
        connected: row.live,
        connectable: wired && !row.live && !pending && !missingCli,
        description,
        id: row.id,
        name: row.name,
        pending
      }
    })
}
