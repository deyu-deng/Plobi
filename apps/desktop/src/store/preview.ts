import { atom, computed } from 'nanostores'

import { normalize } from '@/lib/text'

import { $activeSessionId, $selectedStoredSessionId } from './session'
import { requestWorkbenchFileOpen } from './workbench-tabs'

export interface PreviewTarget {
  binary?: boolean
  byteSize?: number
  /** Inline image bytes (a `data:` URL) when the renderer already holds them —
   * e.g. a pasted/dropped screenshot whose only on-disk copy is a transient
   * path the preview can't reliably re-read. Rendered directly and NOT
   * persisted to the session-preview registry (it would bloat localStorage). */
  dataUrl?: string
  kind: 'file' | 'url'
  label: string
  large?: boolean
  language?: string
  mimeType?: string
  path?: string
  previewKind?: 'binary' | 'html' | 'image' | 'text'
  renderMode?: 'preview' | 'source'
  source: string
  url: string
}

export interface PreviewServerRestart {
  message?: string
  status: 'complete' | 'error' | 'running'
  taskId: string
  url: string
}

export type PreviewRecordSource = 'explicit-link' | 'file-browser' | 'manual' | 'tool-result'

export interface SessionPreviewRecord {
  autoOpen?: boolean
  createdAt: number
  dismissedAt?: number
  id: string
  normalized: PreviewTarget
  sessionId: string
  source: PreviewRecordSource
  target: string
}

type SessionPreviewRegistry = Record<string, SessionPreviewRecord[]>

const REGISTRY_STORAGE_KEY = 'plobi.desktop.sessionPreviews.v1'
const MAX_RECORDS_PER_SESSION = 1
const MAX_SESSIONS = 120

/**
 * 当前**开着**的那个预览目标。
 *
 * 预览轨删掉之后（用户 11-09 11:47：「不要那个预览轨了。删掉它的实现逻辑。」），
 * 预览落在**工作区卡片的页签**里 ⇒ 这枚原子记的是「工作区卡片此刻开着的是哪一个」。
 * 它回答两个问题：这一条预览是不是已经开着了（附件卡那个按钮的高亮）、
 * 以及从预览里选一段文字时该标成哪个文件（`previewSelectionLabel`）。
 */
export const $activePreviewTarget = atom<PreviewTarget | null>(null)

export const $previewReloadRequest = atom(0)
export const $previewServerRestart = atom<PreviewServerRestart | null>(null)
export const $previewServerRestartStatus = computed($previewServerRestart, restart => restart?.status ?? 'idle')
export const $sessionPreviewRegistry = atom<SessionPreviewRegistry>(loadSessionPreviewRegistry())

$sessionPreviewRegistry.subscribe(persistSessionPreviewRegistry)

export function isSamePreviewTarget(a: PreviewTarget | null, b: PreviewTarget | null): boolean {
  if (a === b) {
    return true
  }

  if (!a || !b) {
    return false
  }

  return (
    a.kind === b.kind &&
    a.label === b.label &&
    a.renderMode === b.renderMode &&
    a.source === b.source &&
    a.url === b.url
  )
}

// Manual/file-browser opens are "peeking at a file" → source view in the file
// pane. Tool/explicit-link opens are runnable artifacts → live preview.
function isFilePreviewSource(source: PreviewRecordSource): boolean {
  return source === 'file-browser' || source === 'manual'
}

function previewTargetForSource(target: PreviewTarget, source: PreviewRecordSource): PreviewTarget {
  if (target.kind !== 'file' || target.previewKind !== 'html') {
    return target
  }

  return { ...target, renderMode: isFilePreviewSource(source) ? 'source' : 'preview' }
}

function isPreviewTarget(value: unknown): value is PreviewTarget {
  if (!value || typeof value !== 'object') {
    return false
  }

  const r = value as Record<string, unknown>

  return (
    (r.kind === 'file' || r.kind === 'url') &&
    typeof r.label === 'string' &&
    typeof r.source === 'string' &&
    typeof r.url === 'string'
  )
}

function isPreviewRecord(value: unknown): value is SessionPreviewRecord {
  if (!value || typeof value !== 'object') {
    return false
  }

  const r = value as Record<string, unknown>

  return (
    typeof r.createdAt === 'number' &&
    typeof r.id === 'string' &&
    isPreviewTarget(r.normalized) &&
    typeof r.sessionId === 'string' &&
    ['explicit-link', 'file-browser', 'manual', 'tool-result'].includes(String(r.source)) &&
    typeof r.target === 'string' &&
    (r.dismissedAt === undefined || typeof r.dismissedAt === 'number')
  )
}

function loadSessionPreviewRegistry(): SessionPreviewRegistry {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(REGISTRY_STORAGE_KEY)

    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as unknown

    if (!parsed || typeof parsed !== 'object') {
      return {}
    }

    const out: SessionPreviewRegistry = {}

    for (const [sessionId, records] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(records)) {
        continue
      }

      const valid = records.filter(isPreviewRecord).slice(0, MAX_RECORDS_PER_SESSION)

      if (valid.length > 0) {
        out[sessionId] = valid
      }
    }

    return pruneRegistry(out)
  } catch {
    return {}
  }
}

function persistSessionPreviewRegistry(registry: SessionPreviewRegistry) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    // Drop the inline image bytes before persisting — a screenshot data URL is
    // megabytes and would blow the localStorage quota. On reload the record
    // falls back to reading its `path`/`url`.
    const lean = JSON.stringify(pruneRegistry(registry), (key, value) => (key === 'dataUrl' ? undefined : value))
    window.localStorage.setItem(REGISTRY_STORAGE_KEY, lean)
  } catch {
    // Session previews are a desktop convenience; storage failures are nonfatal.
  }
}

function pruneRegistry(registry: SessionPreviewRegistry): SessionPreviewRegistry {
  const entries = Object.entries(registry)
    .map(
      ([sessionId, records]) =>
        [sessionId, [...records].sort((a, b) => b.createdAt - a.createdAt).slice(0, MAX_RECORDS_PER_SESSION)] as const
    )
    .filter(([, records]) => records.length > 0)
    .sort(([, a], [, b]) => (b[0]?.createdAt ?? 0) - (a[0]?.createdAt ?? 0))
    .slice(0, MAX_SESSIONS)

  return Object.fromEntries(entries)
}

function currentPreviewSessionId(): string {
  return $selectedStoredSessionId.get() || $activeSessionId.get() || ''
}

function recordId(sessionId: string, target: PreviewTarget): string {
  return `${sessionId}:${target.url}`
}

export function registerSessionPreview(
  sessionId: string | null | undefined,
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  const id = sessionId?.trim()

  if (!id) {
    return null
  }

  const current = $sessionPreviewRegistry.get()
  const now = Date.now()
  const records = current[id] ?? []
  const existing = records.find(record => record.normalized.url === target.url)
  const normalized = previewTargetForSource(target, source)

  const nextRecord: SessionPreviewRecord = {
    autoOpen: true,
    createdAt: now,
    id: existing?.id || recordId(id, target),
    normalized,
    sessionId: id,
    source,
    target: rawTarget || target.source
  }

  $sessionPreviewRegistry.set(
    pruneRegistry({
      ...current,
      [id]: [nextRecord]
    })
  )

  return nextRecord
}

/**
 * 打开一个预览 —— 落点是**工作区卡片的一个页签**。
 *
 * 以前这条函数把目标塞进**对话旁那条预览轨**（`setPreviewTarget` ＋
 * `selectRightRailTab(RIGHT_RAIL_PREVIEW_TAB_ID)`），那条轨已经删掉了
 * （用户 11-09 11:47：「以后只有两张卡片了吗？一个叫工作区卡片。一个叫对话区卡片。」）。
 * 现在唯一的去处是 `requestWorkbenchFileOpen`：调用方只管「我要开这个」，
 * 真正的页签由 `desktop-controller.tsx` 落进工作区卡片。
 */
export function setSessionPreviewTarget(
  sessionId: string | null | undefined,
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  const normalized = previewTargetForSource(target, source)

  // 文件浏览/手动打开的是「看一眼这个文件」，不进会话的预览记忆；
  // 工具产出与显式链接才是这个会话的「那一份预览」，回来时还能再开。
  const record = isFilePreviewSource(source) ? null : registerSessionPreview(sessionId, target, source, rawTarget)

  $activePreviewTarget.set(normalized)
  requestWorkbenchFileOpen(normalized, normalized.label)

  return record
}

export function setCurrentSessionPreviewTarget(
  target: PreviewTarget,
  source: PreviewRecordSource,
  rawTarget = target.source
): SessionPreviewRecord | null {
  return setSessionPreviewTarget(currentPreviewSessionId(), target, source, rawTarget)
}

export function getSessionPreviewRecord(sessionId: string | null | undefined): SessionPreviewRecord | null {
  const id = sessionId?.trim()

  if (!id) {
    return null
  }

  return $sessionPreviewRegistry.get()[id]?.find(record => !record.dismissedAt && record.autoOpen !== false) ?? null
}

export function clearSessionPreviewRegistry() {
  $sessionPreviewRegistry.set({})
  $activePreviewTarget.set(null)
}

/**
 * Label for a text selection that came from outside a terminal (the file-preview
 * pane shares ⌘/Ctrl+L with it), so the composer ref reads as a file quote rather
 * than a bogus "zsh:N lines".
 */
export function previewSelectionLabel(): string {
  const target = $activePreviewTarget.get()
  const source = target?.path || target?.url || ''

  return source.split(/[/\\]/).filter(Boolean).pop() || target?.label?.trim() || ''
}

export function requestPreviewReload() {
  $previewReloadRequest.set($previewReloadRequest.get() + 1)
}

export function beginPreviewServerRestart(taskId: string, url: string) {
  $previewServerRestart.set({ status: 'running', taskId, url })
}

export function completePreviewServerRestart(taskId: string, text: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId) {
    return
  }

  $previewServerRestart.set({
    ...current,
    message: text,
    status: normalize(text).startsWith('error:') ? 'error' : 'complete'
  })
}

export function progressPreviewServerRestart(taskId: string, text: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId || current.status !== 'running') {
    return
  }

  $previewServerRestart.set({
    ...current,
    message: text
  })
}

export function failPreviewServerRestart(taskId: string, message: string) {
  const current = $previewServerRestart.get()

  if (current?.taskId !== taskId || current.status !== 'running') {
    return
  }

  $previewServerRestart.set({
    ...current,
    message,
    status: 'error'
  })
}
