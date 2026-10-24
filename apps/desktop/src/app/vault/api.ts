import type { VaultAgentPage, VaultIndex } from './types'

// Same §5 envelope the console API uses: `{ ok, data | error }`. An `ok: false`
// reply is thrown, never rendered as an empty page — "no data" and "read failed"
// must stay distinguishable.
function unwrap<T>(payload: unknown): T {
  if (!payload || typeof payload !== 'object' || !('ok' in payload)) {
    // A 401/404 from the server arrives as `{ detail }`, not as our envelope.
    // Treating that as data would render "no agents yet" for a failed read.
    throw new Error('unexpected vault response')
  }
  const envelope = payload as { ok: boolean; data?: T; error?: string }
  if (!envelope.ok) {
    throw new Error(envelope.error || 'vault request failed')
  }
  if (!('data' in envelope) || envelope.data === undefined) {
    throw new Error(envelope.error || 'vault request failed')
  }
  return envelope.data
}

const TIMEOUT_MS = 20_000

export async function fetchVaultIndex(): Promise<VaultIndex> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/vault/agents', timeoutMs: TIMEOUT_MS })
  return unwrap<VaultIndex>(payload)
}

export async function fetchVaultAgent(name: string): Promise<VaultAgentPage> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/agents/${encodeURIComponent(name)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrap<VaultAgentPage>(payload)
}
