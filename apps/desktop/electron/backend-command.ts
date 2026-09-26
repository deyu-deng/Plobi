'use strict'

// Backend subcommand routing for the desktop-managed Plobi process.
//
// The desktop app launches its own headless backend via `plobi serve` — it
// must NEVER depend on or launch the browser `dashboard`. But `serve` is a
// newer subcommand: a runtime that predates it (an older managed install the
// app hasn't updated yet, or an older `plobi` resolved from PATH) only knows
// `dashboard --no-open`. To avoid bricking those users mid-upgrade we detect
// whether the resolved runtime understands `serve` and, only when it does not,
// fall back to the legacy `dashboard --no-open` invocation. Both produce the
// exact same headless gateway; `serve` is just the decoupled name.
//
// These helpers are pure so they can be unit-tested without Electron.
//
// WP-H1-LAN: when the user opts into the LAN App by setting PLOBI_LAN=1
// (typically via $PLOBI_HOME/.env), the serve subprocess binds
// 0.0.0.0:8787 instead of the default 127.0.0.1:<ephemeral>.  The Electron
// main process already passes through env vars — see main.ts spawn() — but
// we ALSO honour $PLOBI_HOME/.env so a user who set PLOBI_LAN in that
// file before starting Electron still gets the LAN bind.

import fs from 'node:fs'

/** Token name we honour in $PLOBI_HOME/.env (single-line KEY=VALUE). */
const LAN_ENV_KEY = 'PLOBI_LAN'

/** LAN-mode host/port — see WP-H1-LAN contract. */
const LAN_BIND_HOST = '0.0.0.0'
const LAN_BIND_PORT = '8787'

/** Default loopback mode — unchanged from the pre-WP-H1-LAN behaviour. */
const LOOPBACK_HOST = '127.0.0.1'
const LOOPBACK_PORT = '0'

/**
 * Read a single KEY=VALUE line from *path*. Returns the trimmed value when
 * the key is present and well-formed, ``null`` otherwise.
 *
 * Intentionally tiny — we only parse `PLOBI_LAN` from the user's
 * ``$PLOBI_HOME/.env`` so a desktop process that did not inherit the env
 * (Electron launchers, packaged installers, custom wrappers) can still
 * opt into the LAN bind. Five lines of regex, no dotenv dependency: the
 * deserialization surface stays narrow enough to review in one sitting.
 *
 * Returns ``null`` on any I/O or parse failure; never throws. Empty file,
 * missing key, malformed line, or non-existent path all collapse to the
 * same `null` so the caller can fall back to ``process.env`` silently.
 */
export function readEnvValue(path: string | undefined, key: string): string | null {
  if (!path || !key) {
    return null
  }
  let text: string
  try {
    text = fs.readFileSync(path, 'utf8')
  } catch {
    return null
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) {
      continue
    }
    const eq = line.indexOf('=')
    if (eq <= 0) {
      continue
    }
    const candidateKey = line.slice(0, eq).trim()
    if (candidateKey !== key) {
      continue
    }
    let value = line.slice(eq + 1).trim()
    // Quoted form: take everything between the outer matching quotes and
    // drop anything after them (the canonical `.env` shape is
    // ``KEY="value" # comment`` — the comment lives outside the quotes).
    if (
      (value.startsWith('"') && value.includes('"', 1)) ||
      (value.startsWith("'") && value.includes("'", 1))
    ) {
      const quote = value[0]
      const close = value.indexOf(quote, 1)
      if (close !== -1) {
        value = value.slice(1, close)
      }
    } else {
      // Unquoted form: drop a trailing inline comment only when preceded
      // by whitespace, so a value like ``https://x.test?a=b#c`` survives
      // verbatim.
      const commentMatch = value.match(/\s+#.*$/)
      if (commentMatch && typeof commentMatch.index === 'number') {
        value = value.slice(0, commentMatch.index).trim()
      }
    }
    return value || null
  }
  return null
}

/**
 * Resolve the PLOBI_LAN truthy value, preferring the inherited process env
 * and falling back to *plobiHomeEnvPath* (typically ``$PLOBI_HOME/.env``).
 *
 * Equality check is intentionally strict: only the literal string ``"1"``
 * opts into the LAN bind, matching the contract spelled out in
 * Docs/API.md and Docs/ENV-SETUP.md. We do NOT honour ``true`` /
 * ``yes`` / ``on`` here because the user's mental model is "the binary
 * switch", not a truthy-string set — the dashboard / API layer can do
 * more elaborate parsing if it ever needs to.
 */
export function resolveLanMode(env: NodeJS.ProcessEnv, plobiHomeEnvPath?: string): boolean {
  const inherited = typeof env.PLOBI_LAN === 'string' ? env.PLOBI_LAN.trim() : ''
  if (inherited === '1') {
    return true
  }
  if (inherited) {
    // Explicit non-truthy value in the shell wins — don't second-guess it.
    return false
  }
  const fromFile = readEnvValue(plobiHomeEnvPath, LAN_ENV_KEY)
  return fromFile === '1'
}

/**
 * Build the canonical headless backend argv (always `serve`).
 *
 * Default: ``serve --host 127.0.0.1 --port 0`` (loopback + OS-assigned
 * ephemeral port, matching pre-WP-H1-LAN behaviour).
 *
 * When :data:`PLOBI_LAN` is set to ``"1"`` (in either the inherited
 * process env or ``$PLOBI_HOME/.env``), returns ``serve --host 0.0.0.0
 * --port 8787`` so the Plobi App on the same Wi-Fi can reach the
 * desktop. Any other value leaves the bind on loopback.
 *
 * @param {string} [profile] optional Plobi profile to pin via ``--profile``.
 * @param {object} [options] optional overrides for testing.
 * @param {NodeJS.ProcessEnv} [options.env] process env to read (default ``process.env``).
 * @param {string} [options.plobiHomeEnvPath] path to ``$PLOBI_HOME/.env`` to fall back on.
 */
export function serveBackendArgs(
  profile?: string,
  options?: { env?: NodeJS.ProcessEnv; plobiHomeEnvPath?: string },
) {
  const head = profile ? ['--profile', profile] : []
  const env = options?.env ?? process.env
  const plobiHomeEnvPath =
    options?.plobiHomeEnvPath ?? (env.PLOBI_HOME ? `${env.PLOBI_HOME}/.env` : undefined)

  if (resolveLanMode(env, plobiHomeEnvPath)) {
    return [...head, 'serve', '--host', LAN_BIND_HOST, '--port', LAN_BIND_PORT]
  }
  return [...head, 'serve', '--host', LOOPBACK_HOST, '--port', LOOPBACK_PORT]
}

/**
 * Rewrite a resolved backend argv from `serve` to the legacy
 * `dashboard --no-open` form, preserving every other argument (incl. a leading
 * `-m plobi_cli.main` and any `--profile <name>`). Returns a copy; if there is
 * no `serve` token the argv is returned unchanged.
 */
export function dashboardFallbackArgs(args) {
  const i = args.indexOf('serve')

  if (i === -1) {
    return args.slice()
  }

  return [...args.slice(0, i), 'dashboard', '--no-open', ...args.slice(i + 1)]
}

/**
 * True when a runtime's `plobi_cli/subcommands/dashboard.py` source registers
 * the `serve` subcommand. Matches `add_parser("serve"` / `add_parser('serve'`
 * specifically so the substring "server" (e.g. "start_server", "web server")
 * never produces a false positive.
 */
export function sourceDeclaresServe(dashboardPySource) {
  return /add_parser\(\s*["']serve["']/.test(String(dashboardPySource || ''))
}
