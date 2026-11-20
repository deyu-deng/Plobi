import { atom } from 'nanostores'

import { triggerHaptic } from '@/lib/haptics'

export interface ComposerAttachment {
  id: string
  kind: 'image' | 'file' | 'folder' | 'terminal' | 'url'
  label: string
  detail?: string
  refText?: string
  previewUrl?: string
  path?: string
  attachedSessionId?: string
  /**
   * 正文里那枚 chip 的**值**（线上形态 `@kind:value` 里的 value）。
   *
   * 11-19 起附件也是正文里的一枚 chip ⇒ 这个值是「正文 ↔ 附件」之间唯一的钥匙
   * （发出去的附件从正文里的 chip 反推）。
   *
   * 它得**稳**：上传成功后 `path` 会被改写成网关那边的路径（`image.attach`），
   * 而正文里那枚 chip 的值不变 ⇒ 靠 `path` 认附件就认不出来了。
   */
  chipValue?: string
  /** Set while the file/image bytes are being staged into the session
   * workspace (remote upload or local stage), and 'error' if that failed.
   * Drives the spinner / error state on the composer attachment card. */
  uploadState?: 'uploading' | 'error'
}

export const $composerDraft = atom('')
export const $composerAttachments = atom<ComposerAttachment[]>([])
export const $composerTerminalSelections = atom<Record<string, string>>({})

// Per-thread draft stash for the decoupled composer. Session lifecycle never
// touches this — only ChatBar's scope swap reads/writes it. Text mirrors to
// localStorage; attachments are memory-only (blobs, upload state).
export const SESSION_DRAFTS_STORAGE_KEY = 'plobi:composer-drafts:v3'

const NEW_SESSION_DRAFT_KEY = '__new__'
const MAX_PERSISTED_DRAFTS = 50
const EMPTY_SESSION_DRAFT: SessionDraft = { attachments: [], text: '' }

export interface SessionDraft {
  attachments: ComposerAttachment[]
  text: string
}

const draftKey = (scope: string | null | undefined) => {
  const trimmed = scope?.trim()

  if (!trimmed) {
    return NEW_SESSION_DRAFT_KEY
  }

  // 草稿的桶＝队列的桶＝**Agent 的作用域键**（`app/chat/composer/queue-key.ts`）：
  // 切 Agent 换一份草稿，同一个 Agent 回来草稿还在。键不变 ⇒ 草稿不会凭空不见。
  return trimmed
}

const cloneDraft = (draft: SessionDraft): SessionDraft => ({
  attachments: draft.attachments.map(attachment => ({ ...attachment })),
  text: draft.text
})

function loadPersistedDraftTexts(): [string, SessionDraft][] {
  try {
    const raw = window.localStorage.getItem(SESSION_DRAFTS_STORAGE_KEY)

    if (!raw) {
      return []
    }

    // 落盘的那一份草稿：正文 ＋ 附件记录（**不含缩略图**——data URL 可能几兆，
    // localStorage 装不下）。附件记录要跟着落盘：附件也是正文里的一枚 chip 了，
    // 记录丢了那枚 chip 就成了空壳（发出去时不带它）。
    //
    // 旧格式（只有正文、没有附件记录）照读：那会儿附件不是 chip，正文里也没有
    // 它们的线上形态。
    return Object.entries(JSON.parse(raw) as Record<string, { attachments?: unknown; text?: string } | string>).map(
      ([key, entry]) => {
        if (typeof entry === 'string') {
          return [key, { attachments: [], text: entry }] as const
        }

        return [
          key,
          {
            attachments: Array.isArray(entry?.attachments) ? (entry.attachments as ComposerAttachment[]) : [],
            text: typeof entry?.text === 'string' ? entry.text : ''
          }
        ] as const
      }
    )
  } catch {
    return []
  }
}

const draftsBySession = new Map<string, SessionDraft>(loadPersistedDraftTexts())

/** 落盘的那一份附件：去掉缩略图（字节）与上传状态（那是一次性的）。 */
function persistableAttachments(attachments: ComposerAttachment[]): ComposerAttachment[] {
  return attachments.map(({ previewUrl, uploadState, ...rest }) => rest)
}

function persistDraftTexts() {
  try {
    const entries = [...draftsBySession]
      .filter(([, draft]) => draft.text)
      .slice(-MAX_PERSISTED_DRAFTS)
      .map(
        ([key, draft]) =>
          [key, { attachments: persistableAttachments(draft.attachments), text: draft.text }] as const
      )

    if (entries.length === 0) {
      window.localStorage.removeItem(SESSION_DRAFTS_STORAGE_KEY)
    } else {
      window.localStorage.setItem(SESSION_DRAFTS_STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)))
    }
  } catch {
    // Best-effort only — quota/private-mode must never break typing.
  }
}

export function stashSessionDraft(scope: string | null | undefined, text: string, attachments: ComposerAttachment[]) {
  const key = draftKey(scope)

  // Delete-then-set keeps MRU order for MAX_PERSISTED_DRAFTS eviction.
  draftsBySession.delete(key)

  if (text.trim() || attachments.length > 0) {
    draftsBySession.set(key, cloneDraft({ attachments, text }))
  }

  persistDraftTexts()
}

export function takeSessionDraft(scope: string | null | undefined): SessionDraft {
  const stashed = draftsBySession.get(draftKey(scope))

  return stashed ? cloneDraft(stashed) : EMPTY_SESSION_DRAFT
}

export const clearSessionDraft = (scope: string | null | undefined) => stashSessionDraft(scope, '', [])

export function setComposerDraft(value: string) {
  $composerDraft.set(value)
}

export function appendComposerDraft(value: string) {
  const text = value.trim()

  if (!text) {
    return
  }

  const current = $composerDraft.get()
  const separator = current && !current.endsWith('\n') ? '\n\n' : ''

  $composerDraft.set(`${current}${separator}${text}`)
}

export function appendComposerInline(value: string) {
  const text = value.trim()

  if (!text) {
    return
  }

  const current = $composerDraft.get().trimEnd()
  const separator = current ? ' ' : ''

  $composerDraft.set(`${current}${separator}${text}`)
}

export function clearComposerDraft() {
  $composerDraft.set('')
}

export function addComposerAttachment(attachment: ComposerAttachment) {
  const previous = $composerAttachments.get()
  const next = upsertAttachment(previous, attachment)
  $composerAttachments.set(next)

  if (next.length > previous.length && attachment.kind !== 'url') {
    triggerHaptic('selection')
  }
}

export function removeComposerAttachment(id: string): ComposerAttachment | null {
  const current = $composerAttachments.get()
  const removed = current.find(attachment => attachment.id === id) || null
  $composerAttachments.set(current.filter(attachment => attachment.id !== id))

  return removed
}

/** Replace an existing attachment in place by id. No-op (returns false) when the
 * id is gone — e.g. the user removed the chip while an eager upload was still in
 * flight, so a late success must NOT resurrect it. Use this instead of
 * addComposerAttachment for async results that may land after a removal. */
export function updateComposerAttachment(attachment: ComposerAttachment): boolean {
  const current = $composerAttachments.get()
  const index = current.findIndex(item => item.id === attachment.id)

  if (index < 0) {
    return false
  }

  const next = [...current]
  next[index] = attachment
  $composerAttachments.set(next)

  return true
}

export function clearComposerAttachments() {
  $composerAttachments.set([])
}

/** Update only the upload state of an existing attachment (no-op if it's gone,
 * e.g. the user removed it mid-upload). Pass `undefined` to clear it. */
export function setComposerAttachmentUploadState(id: string, uploadState?: ComposerAttachment['uploadState']) {
  const current = $composerAttachments.get()
  const index = current.findIndex(attachment => attachment.id === id)

  if (index < 0) {
    return
  }

  const next = [...current]
  next[index] = { ...next[index]!, uploadState }
  $composerAttachments.set(next)
}

const TERMINAL_REF_RE = /@terminal:(`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|\S+)/g

/** 线上形态里的值（`@file:`path`` ⇒ `path`）：去掉那圈引号与尾标点。 */
export function unquoteRefValue(raw: string) {
  const head = raw[0]
  const tail = raw[raw.length - 1]
  const quoted = (head === '`' && tail === '`') || (head === '"' && tail === '"') || (head === "'" && tail === "'")

  return (quoted ? raw.slice(1, -1) : raw).replace(/[,.;!?]+$/, '').trim()
}

/**
 * 附件在正文里那枚 chip 的**值**（线上形态 `@kind:value` 里的 value）。
 *
 * 取值的次序：显式记下的 `chipValue` → `refText` 里的值（文件／文件夹／网址）→ 路径。
 */
export function attachmentChipValue(attachment: ComposerAttachment): string {
  if (attachment.chipValue) {
    return attachment.chipValue
  }

  if (attachment.refText) {
    const [, , rawValue] = /^@([^:]+):(.+)$/.exec(attachment.refText) ?? []

    if (rawValue) {
      return unquoteRefValue(rawValue)
    }
  }

  return attachment.path || ''
}

/** 补上 `chipValue`（附件进正文前先把这个钥匙钉好）。 */
export function withChipValue(attachment: ComposerAttachment): ComposerAttachment {
  return { ...attachment, chipValue: attachmentChipValue(attachment) }
}

/** 附件自己那一行是哪个 kind（`@file:` / `@image:` ……）。 */
export function attachmentKindOf(attachment: ComposerAttachment): string {
  const match = /^@([^:]+):/.exec(attachment.refText || '')

  return match?.[1] || attachment.kind
}

/**
 * 那枚 chip（kind + value）对得上的附件。
 *
 * 认附件看三样：`chipValue`（正文里那枚 chip 的值，最准）、`id`、路径。
 * **kind 也要对得上**——一枚 `@file:` 的 chip 不该捡到同一路径的图。
 */
export function attachmentForChipValue(kind: string, value: string): ComposerAttachment | undefined {
  if (!kind || !value) {
    return undefined
  }

  return $composerAttachments.get().find(item => {
    // 附件表里可能有空洞（切会话／恢复草稿会留下 undefined 的行——见 #49624）。
    if (!item) {
      return false
    }

    if (attachmentKindOf(item) !== kind) {
      return false
    }

    return attachmentChipValue(item) === value || item.id === value || item.path === value
  })
}

function terminalLabelsFromDraft(draft: string) {
  const labels: string[] = []
  const seen = new Set<string>()

  for (const match of draft.matchAll(TERMINAL_REF_RE)) {
    const label = unquoteRefValue(match[1] || '')

    if (!label || seen.has(label)) {
      continue
    }

    seen.add(label)
    labels.push(label)
  }

  return labels
}

export function setComposerTerminalSelection(label: string, text: string) {
  const nextLabel = label.trim()
  const nextText = text.trim()

  if (!nextLabel || !nextText) {
    return
  }

  const current = $composerTerminalSelections.get()

  if (current[nextLabel] === nextText) {
    return
  }

  $composerTerminalSelections.set({
    ...current,
    [nextLabel]: nextText
  })
}

export function reconcileComposerTerminalSelections(draft: string) {
  const current = $composerTerminalSelections.get()
  const labels = new Set(terminalLabelsFromDraft(draft))
  let changed = false
  const next: Record<string, string> = {}

  for (const [label, text] of Object.entries(current)) {
    if (!labels.has(label)) {
      changed = true

      continue
    }

    next[label] = text
  }

  if (changed) {
    $composerTerminalSelections.set(next)
  }
}

export function terminalContextBlocksFromDraft(draft: string) {
  const labels = terminalLabelsFromDraft(draft)

  if (labels.length === 0) {
    return []
  }

  const selections = $composerTerminalSelections.get()

  return labels.flatMap(label => {
    const text = selections[label]?.trim()

    if (!text) {
      return []
    }

    return `\`\`\`terminal\n${text}\n\`\`\``
  })
}

export function clearComposerTerminalSelections() {
  if (Object.keys($composerTerminalSelections.get()).length === 0) {
    return
  }

  $composerTerminalSelections.set({})
}

function upsertAttachment(attachments: ComposerAttachment[], attachment: ComposerAttachment) {
  const index = attachments.findIndex(item => item.id === attachment.id)

  if (index < 0) {
    return [...attachments, attachment]
  }

  const next = [...attachments]
  next[index] = attachment

  return next
}
