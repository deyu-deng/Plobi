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
  merged: number
  committed: boolean
  commit: string
  detail: string
}

export interface DiaryCaptureResult extends DiarySaveResult {
  line: string
}

const READ_TIMEOUT_MS = 20_000
// 保存那一下要落盘 + 只提交这一篇（跑脑侧 git），给足时间。
const WRITE_TIMEOUT_MS = 60_000

export function todayISO(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export function shiftDay(day: string, delta: number): string {
  const [year, month, date] = day.split('-').map(Number)
  const shifted = new Date(year, month - 1, date + delta)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(shifted.getDate())}`
}

export async function fetchDiaryIndex(): Promise<DiaryIndex> {
  const payload = await window.plobiDesktop.api<unknown>({ path: '/api/vault/diary', timeoutMs: READ_TIMEOUT_MS })
  return unwrapEnvelope<DiaryIndex>(payload, 'diary')
}

export async function fetchDiaryEntry(date: string): Promise<DiaryEntry> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/diary/${encodeURIComponent(date)}`,
    timeoutMs: READ_TIMEOUT_MS
  })
  return unwrapEnvelope<DiaryEntry>(payload, 'diary')
}

export async function saveDiaryEntry(date: string, text: string, base?: string): Promise<DiarySaveResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/diary/${encodeURIComponent(date)}`,
    method: 'PUT',
    body: base === undefined ? { text } : { text, base },
    timeoutMs: WRITE_TIMEOUT_MS
  })
  return unwrapEnvelope<DiarySaveResult>(payload, 'diary')
}

export async function captureDiaryLine(date: string, line: string): Promise<DiaryCaptureResult> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/vault/diary/${encodeURIComponent(date)}/capture`,
    method: 'POST',
    body: { line },
    timeoutMs: WRITE_TIMEOUT_MS
  })
  return unwrapEnvelope<DiaryCaptureResult>(payload, 'diary')
}
