/**
 * aigw-key-reconcile.ts
 *
 * The desktop shell resolves a gateway key from its environment and, when
 * nothing is set, falls back to a literal it carries itself. That default is
 * allowed -- the user ruled on 2026-10-08 that the gateway itself owns the only
 * definition (甲) while consumers may keep a fallback as long as they reconcile
 * against it (乙); see the `WP-AIGW-KEY-LITERALS` task brief. What is *not*
 * allowed is the shell silently keeping a copy that no longer matches what the
 * gateway actually accepts. Today that consistency is held up by a sentence in
 * `aigw/config.yaml`'s comment, not by code, so a changed gateway key and an
 * unchanged shell default produce auth failures that read like a dead key (the
 * exact misdiagnosis this workspace has already paid for twice).
 *
 * So this module answers one question before the shell uses its key: does the
 * value I resolved match the value the gateway declares? The declaration is
 * parsed to be **byte-identical with the Python reader** --
 * `plobi/agents/registry.py` `_GATEWAY_KEY_PLACEHOLDER` / `gateway_declared_api_key()`
 * -- because a second parser that merely *looks* the same is how a legitimate
 * config produces a phantom mismatch (裁定 94 measured four such drifts: a
 * lowercase placeholder name, a default containing `}`, doubled quotes, and an
 * empty value). `aigw-key-reconcile.parity.test.ts` pins both sides to one
 * table; changing Python's semantics without that table goes red on purpose.
 *
 * Nothing here returns or logs a secret. Every value that leaves this module
 * is either a source label or a SHA-256 prefix, so the reconciliation result
 * is safe to put in the log ring, in `doctor` output, and in test snapshots.
 * A config we could not read is reported as `NOT CHECKED` rather than as a
 * pass -- "we could not tell" must not become "we assume consistent". An empty
 * declared value is a different fact: Python treats it as *no declaration* and
 * falls back to the shared development default, so we do the same, and calling
 * that a mismatch would be the false red 裁定 94 item 2 exists to kill.
 */
import { createHash } from 'node:crypto'

/** Same order, same names as `main.ts`'s `productQuotaApiKey()` -- pinned there. */
export const ENV_ORDER = ['AIGW_API_KEY', 'PLOBI_AIGW_API_KEY'] as const
/** Same literal as Python's `AIGW_DEV_DEFAULT_API_KEY`. */
export const DEV_DEFAULT_API_KEY = 'sk-local-dev-key'
/** Same label Python's `aigw_credential_source()` prints, so one report has one vocabulary. */
export const DEV_DEFAULT_SOURCE_LABEL = 'development default'

/** The gateway's declared key, or `''` when the config declares nothing usable (Python's own semantics). */
export function declaredGatewayApiKey(
  configText: string | null,
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  if (configText === null) return null

  let inServer = false
  for (const raw of configText.split(/\r?\n/)) {
    const line = raw.split('#', 1)[0].replace(/\s+$/, '')
    if (!line.trim()) continue
    if (/^server:\s*$/.test(line)) {
      inServer = true
      continue
    }
    // A new top-level key closes the `server:` block.
    if (inServer && /^\S/.test(line)) inServer = false
    if (!inServer) continue

    const match = /^\s+api_key:\s*(.+?)\s*$/.exec(line)
    if (!match) continue
    // Python: `value.strip("\"'")` -- every leading/trailing quote char, not one.
    const value = match[1].replace(/^["']+|["']+$/g, '').trim()
    // Python: `^\$\{([A-Z0-9_]+):-([^}]*)\}$` -- uppercase-only name, and a
    // default that cannot contain `}`. Anything else is not a placeholder here.
    const placeholder = /^\$\{([A-Z0-9_]+):-([^}]*)\}$/.exec(value)
    if (placeholder) {
      const fromEnv = (env[placeholder[1]] || '').trim()
      return fromEnv || placeholder[2].trim()
    }
    return value
  }
  return ''
}

/** What the shell itself would use, plus which rung of its ladder produced it. */
export function usedGatewayKey(
  env: NodeJS.ProcessEnv = process.env,
  devDefault = DEV_DEFAULT_API_KEY,
): { value: string; source: string } {
  for (const name of ENV_ORDER) {
    const v = (env[name] || '').trim()
    if (v) return { value: v, source: `env:${name}` }
  }
  return { value: devDefault, source: DEV_DEFAULT_SOURCE_LABEL }
}

/** Short enough to recognise, long enough to compare, never reversible. */
export function fingerprintKey(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex').slice(0, 12)
}

export interface KeyReconciliation {
  match: boolean
  usedSource: string
  usedFp: string
  declaredFp: string | null
  /** True when the config file itself could not be read -- the one case that is "we don't know". */
  declaredReadable: boolean
  /** Safe to log verbatim: never contains either key. */
  summary: string
}

export function reconcileGatewayKey(
  configText: string | null,
  env: NodeJS.ProcessEnv = process.env,
  devDefault = DEV_DEFAULT_API_KEY,
): KeyReconciliation {
  const used = usedGatewayKey(env, devDefault)
  const declared = declaredGatewayApiKey(configText, env)
  const usedFp = fingerprintKey(used.value)

  // These summaries reach the tray menu and the boot ledger, so they carry no internal side name (裁定 45.4).
  if (declared === null) {
    return {
      match: false,
      usedSource: used.source,
      usedFp,
      declaredFp: null,
      declaredReadable: false,
      summary: `key NOT CHECKED: gateway config unreadable (used ${used.source}, fp=${usedFp}) — say so, never assume consistent`,
    }
  }

  // `''` means the gateway declares nothing usable. Python then falls back to
  // the same shared development default this ladder's last rung holds, so the
  // comparable value is the default -- not an empty string, which would make a
  // perfectly valid config look broken.
  const effective = declared || devDefault
  const declaredFp = fingerprintKey(effective)
  const match = declaredFp === usedFp
  const declaredNote = declared ? `declared fp=${declaredFp}` : `declares nothing → default fp=${declaredFp}`
  return {
    match,
    usedSource: used.source,
    usedFp,
    declaredFp,
    declaredReadable: true,
    summary: match
      ? `key ok: ${used.source} fp=${usedFp} == gateway ${declaredNote}`
      : `key MISMATCH: shell uses ${used.source} fp=${usedFp} but gateway ${declaredNote} — one of them is stale, and auth failures will look like a dead key`,
  }
}
