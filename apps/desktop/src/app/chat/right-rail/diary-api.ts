import { unwrapEnvelope } from '@/lib/api-envelope'

export interface DiaryEntryMeta {
  date: string
  weekday: string
  filename: string
  chars: number
  updated: string
}

export interface DiaryIndex {
  root: string
  entries: DiaryEntryMeta[]
  today: string
}

export interface DiaryEntry {
  date: string
  weekday: string
  filename: string
  exists: boolean
  text: string
  updated: string
}

export interface DiarySaveResult {
  date: string
  weekday: string
  filename: string
  fsPath: string
  bytes: number
  committed: boolean
  commit: string
  detail: string
}

const TIMEOUT_MS = 20_000

export async function fetchDiaryIndex(): Promise<DiaryIndex> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/vault/diary', timeoutMs: TIMEOUT_MS })
  return unwrapEnvelope<DiaryIndex>(payload, 'diary')
}

export async function fetchDiaryEntry(date: string): Promise<DiaryEntry> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/diary/${encodeURIComponent(date)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrapEnvelope<DiaryEntry>(payload, 'diary')
}

export async function saveDiaryEntry(date: string, text: string): Promise<DiarySaveResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/diary/${encodeURIComponent(date)}`,
    method: 'PUT',
    body: { text },
    timeoutMs: 60_000
  })
  return unwrapEnvelope<DiarySaveResult>(payload, 'diary')
}
