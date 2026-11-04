import { unwrapEnvelope } from '@/lib/api-envelope'

import type { SurfaceAgentPage, SurfaceIndex } from './types'

const TIMEOUT_MS = 20_000

export async function fetchVaultIndex(): Promise<SurfaceIndex> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/surface/agents', timeoutMs: TIMEOUT_MS })
  return unwrapEnvelope<SurfaceIndex>(payload, 'surface')
}

export async function fetchVaultAgent(name: string): Promise<SurfaceAgentPage> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/surface/agents/${encodeURIComponent(name)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrapEnvelope<SurfaceAgentPage>(payload, 'surface')
}
