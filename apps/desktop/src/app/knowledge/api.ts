import { unwrapEnvelope } from '@/lib/api-envelope'

export interface NoteMeta {
  path: string
  title: string
  topic: string
  tags: string[]
  belongs: string[]
  updated: string
  chars: number
  excerpt: string
}

export interface NotesLibrary {
  root: string
  notes: NoteMeta[]
  topics: { name: string; count: number }[]
  tags: string[]
  totals: { scanned: number; listed: number; skipped: number }
}

export interface NotePage {
  path: string
  fsPath: string
  text: string
  chars: number
}

const TIMEOUT_MS = 20_000

export async function fetchNotes(): Promise<NotesLibrary> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/vault/notes', timeoutMs: TIMEOUT_MS })
  return unwrapEnvelope<NotesLibrary>(payload, 'notes')
}

export async function fetchNote(path: string): Promise<NotePage> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/notes/content?path=${encodeURIComponent(path)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrapEnvelope<NotePage>(payload, 'note')
}
