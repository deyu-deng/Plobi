import { unwrapEnvelope } from '@/lib/api-envelope'

export const NOTES_PREFIX = 'Surface/notes/'

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

export interface NoteSaveResult {
  path: string
  fsPath: string
  bytes: number
  created: boolean
  committed: boolean
  commit: string
  detail: string
}

const TIMEOUT_MS = 20_000
// 保存要落盘 + 只提交这一篇（跑 surface 仓的 git），给足时间。
const WRITE_TIMEOUT_MS = 60_000

export async function fetchNotes(): Promise<NotesLibrary> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/surface/notes', timeoutMs: TIMEOUT_MS })
  return unwrapEnvelope<NotesLibrary>(payload, 'notes')
}

export async function fetchNote(path: string): Promise<NotePage> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/surface/notes/content?path=${encodeURIComponent(path)}`,
    timeoutMs: TIMEOUT_MS
  })
  return unwrapEnvelope<NotePage>(payload, 'note')
}

/**
 * 人在笔记栏里整篇存回。`base` 是打开编辑器时那份原文：盘上被别处改过，
 * 后端会回 409 而不是覆盖掉那些字。`create` 只在新建时显式给 true——
 * 保存已有笔记时路径拼错，不该静默写出一个新文件。
 */
export async function saveNote(
  path: string,
  text: string,
  options: { base?: string; create?: boolean } = {}
): Promise<NoteSaveResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/surface/notes/content?path=${encodeURIComponent(path)}`,
    method: 'PUT',
    body: { text, base: options.base ?? '', create: options.create === true },
    timeoutMs: WRITE_TIMEOUT_MS
  })
  return unwrapEnvelope<NoteSaveResult>(payload, 'note')
}
