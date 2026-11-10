import { unwrapEnvelope } from '@/lib/api-envelope'

export const NOTES_PREFIX = 'Notes/'

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

/** 改一篇笔记的名字之后的样子：旧路径、新路径、连带动了哪几篇。 */
export interface NoteRenameResult {
  path: string
  new_path: string
  /** 连带动过的那些（它自己 ＋ 指向它的那些）。 */
  changed: string[]
}

/** 删一篇笔记之后的样子：原路径、它在废纸篓里的落点。 */
export interface NoteDeleteResult {
  path: string
  trash: string
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

/**
 * 给一篇笔记改名字。落盘 + 只提交这一篇（跑 surface 仓的 git），给足时间。
 */
export async function renameNote(path: string, name: string): Promise<NoteRenameResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: '/api/surface/notes/rename',
    method: 'POST',
    body: { path, name },
    timeoutMs: WRITE_TIMEOUT_MS
  })
  return unwrapEnvelope<NoteRenameResult>(payload, 'note rename')
}

/** 删一篇笔记（移进废纸篓）。同样要落盘 + 提交，给足时间。 */
export async function deleteNote(path: string): Promise<NoteDeleteResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: '/api/surface/notes/delete',
    method: 'POST',
    body: { path },
    timeoutMs: WRITE_TIMEOUT_MS
  })
  return unwrapEnvelope<NoteDeleteResult>(payload, 'note delete')
}
