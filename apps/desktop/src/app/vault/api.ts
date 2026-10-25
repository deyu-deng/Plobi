import { unwrapEnvelope } from '@/lib/api-envelope'

import type { VaultAgentPage, VaultIndex } from './types'

const TIMEOUT_MS = 20_000

export async function fetchVaultIndex(): Promise<VaultIndex> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/vault/agents', timeoutMs: TIMEOUT_MS })
  return unwrapEnvelope<VaultIndex>(payload, 'vault')
}

export async function fetchVaultAgent(name: string): Promise<VaultAgentPage> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/agents/${encodeURIComponent(name)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrapEnvelope<VaultAgentPage>(payload, 'vault')
}
