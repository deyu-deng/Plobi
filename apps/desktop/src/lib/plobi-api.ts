/**
 * Frontend client for North Star HTTP API.
 * FE/BE separation: do not import Python lib; call dashboard plugin routes.
 *
 * Base path: /api/plugins/plobi-north-star
 * Contract: docs/plobi/north_star/API.md
 */

export type PlobiArea = 'task' | 'compute' | 'preview' | 'ops'

const BASE = '/api/plugins/plobi-north-star'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init?.headers || {})
    }
  })
  if (!res.ok) {
    throw new Error(`plobi-api ${path} → ${res.status}`)
  }
  return res.json() as Promise<T>
}

export function plobiHealth() {
  return request<{ ok: boolean; public: string[] }>('/health')
}

export function plobiBoard() {
  return request<Record<string, unknown>>('/board')
}

export function plobiTask(body: Record<string, unknown>) {
  return request<Record<string, unknown>>('/task', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}

export function plobiPreviewList(limit = 50) {
  return request<{ items: unknown[] }>(`/preview?limit=${limit}`)
}

export function plobiPreview(body: Record<string, unknown>) {
  return request<Record<string, unknown>>('/preview', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}

export function plobiOps(body: Record<string, unknown>) {
  return request<Record<string, unknown>>('/ops', {
    method: 'POST',
    body: JSON.stringify(body)
  })
}

export function plobiMorningReport() {
  return request<Record<string, unknown>>('/morning-report')
}
