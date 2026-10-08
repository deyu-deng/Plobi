/**
 * aigw-key-reconcile.ts
 *
 * The desktop shell resolves a gateway key from its environment and, when
 * nothing is set, falls back to a literal it carries itself. That default is
 * allowed (裁定 92 的形状：网关本体是值的拥有者只留一处，消费者可以留默认) —
 * what is *not* allowed is the shell silently keeping a copy that no longer
 * matches what the gateway actually accepts. Today that consistency is held
 * up by a sentence in `aigw/config.yaml`'s comment, not by code, so a changed
 * gateway key and an unchanged shell default produce auth failures that read
 * like a dead key (the exact misdiagnosis this workspace has already paid
 * for twice).
 *
 * So this module answers one question before the shell uses its key: does the
 * value I resolved match the value the gateway declares? The declaration is
 * read the same narrow way the Python side reads it (`server.api_key`, no YAML
 * dependency, `${VAR:-default}` expanded from the environment) so the two
 * consumers cannot drift by disagreeing about how to parse the file.
 *
 * Nothing here returns or logs a secret. Every value that leaves this module
 * is either a source label or a SHA-256 prefix, so the reconciliation result
 * is safe to put in the log ring, in `doctor` output, and in test snapshots.
 * An unreadable config is reported as `unknown` rather than as a pass — "we
 * could not tell" must not become "we assume consistent".
 */
import { createHash } from 'node:crypto'

const ENV_ORDER = ['AIGW_API_KEY', 'PLOBI_AIGW_API_KEY'] as const

/** The gateway's declared key, or null when the config declares nothing we can read. */
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
    const value = match[1].replace(/^["']|["']$/g, '').trim()
    const placeholder = /^\$\{([A-Za-z_][A-Za-z0-9_]*):-([\s\S]*)\}$/.exec(value)
    if (placeholder) {
      const fromEnv = (env[placeholder[1]] || '').trim()
      return fromEnv || placeholder[2].trim()
    }
    return value
  }
  return null
}

/** What the shell itself would use, plus which rung of its ladder produced it. */
export function usedGatewayKey(
  env: NodeJS.ProcessEnv = process.env,
  devDefault = 'sk-local-dev-key',
): { value: string; source: string } {
  for (const name of ENV_ORDER) {
    const v = (env[name] || '').trim()
    if (v) return { value: v, source: `env:${name}` }
  }
  return { value: devDefault, source: 'shell_default' }
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
  /** Safe to log verbatim: never contains either key. */
  summary: string
}

export function reconcileGatewayKey(
  configText: string | null,
  env: NodeJS.ProcessEnv = process.env,
  devDefault = 'sk-local-dev-key',
): KeyReconciliation {
  const used = usedGatewayKey(env, devDefault)
  const declared = declaredGatewayApiKey(configText, env)
  const usedFp = fingerprintKey(used.value)

  if (declared === null) {
    return {
      match: false,
      usedSource: used.source,
      usedFp,
      declaredFp: null,
      summary: `aigw key NOT CHECKED: gateway config declares no server.api_key (used ${used.source}, fp=${usedFp}) — say so, never assume consistent`,
    }
  }

  const declaredFp = fingerprintKey(declared)
  const match = declaredFp === usedFp
  return {
    match,
    usedSource: used.source,
    usedFp,
    declaredFp,
    summary: match
      ? `aigw key ok: ${used.source} fp=${usedFp} == gateway declared fp=${declaredFp}`
      : `aigw key MISMATCH: shell uses ${used.source} fp=${usedFp} but gateway declares fp=${declaredFp} — one of them is stale, and auth failures will look like a dead key`,
  }
}
