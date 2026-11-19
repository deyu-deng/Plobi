import { atom } from 'nanostores'

import type { ComposerAttachment } from './composer'

export interface QueuedPromptEntry {
  id: string
  text: string
  attachments: ComposerAttachment[]
  queuedAt: number
  /** 这条排队时引用的是哪几条（裁定 196 ④；11-19 起可多个）；忙完再发也要带着。 */
  replyTo?: string[]
}

type QueueState = Record<string, QueuedPromptEntry[]>

const STORAGE_KEY = 'plobi.desktop.composerQueue.v1'

const load = (): QueueState => {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null

    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as QueueState) : {}
  } catch {
    return {}
  }
}

const save = (state: QueueState) => {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (Object.keys(state).length === 0) {
      window.localStorage.removeItem(STORAGE_KEY)
    } else {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    }
  } catch {
    // best-effort: storage may be unavailable, queue still works in-memory
  }
}

/**
 * 队列的桶 = **Agent 的作用域键**（`l1` / `agent:<id>`，见
 * `app/chat/composer/queue-key.ts` 的 `agentScopeKey`）。
 *
 * 这个产品已经**没有会话概念**了——一 Agent 一张嘴（裁定 37／R-015，
 * `north-star-operating-model.md:89`）⇒「这一场对话」的身份就是它那个 Agent。
 * 队列之前按会话 id 分桶，撞过两个坑：① 一场新对话在拿到会话 id 之前**键是空的**
 * ⇒ 入队静默失败（用户 11-13：「输入后。发送不了了」）；② 同一个 Agent 的新对话
 * 要等会话建出来才搬得动桶（占位桶 → 真会话桶）。
 *
 * 按 Agent 分桶之后这两件事一起没了：桶**永远存在**（L1 一档、每个 L2 一档），
 * 而且**一辈子不变**——会话换了（新建／恢复）桶也不换 ⇒ 不需要搬家，也不会把
 * 同一条消息发两次。
 */
export const $queuedPromptsByAgent = atom<QueueState>(load())

const writeBucket = (bucket: string, queue: QueuedPromptEntry[]) => {
  const current = $queuedPromptsByAgent.get()
  const next = { ...current }

  if (queue.length === 0) {
    delete next[bucket]
  } else {
    next[bucket] = queue
  }

  $queuedPromptsByAgent.set(next)
  save(next)
}

const keyOf = (key: string | null | undefined): null | string => {
  const trimmed = key?.trim()

  return trimmed ? trimmed : null
}

const queueFor = (bucket: string) => $queuedPromptsByAgent.get()[bucket] ?? []

const nextId = () => `queued-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

const cloneAttachments = (attachments: ComposerAttachment[]) => attachments.map(a => ({ ...a }))

export const getQueuedPrompts = (key: string | null | undefined): QueuedPromptEntry[] => {
  const bucket = keyOf(key)

  return bucket ? queueFor(bucket) : []
}

export const enqueueQueuedPrompt = (
  key: string | null | undefined,
  payload: { text: string; attachments: ComposerAttachment[] }
): null | QueuedPromptEntry => {
  const bucket = keyOf(key)

  if (!bucket) {
    return null
  }

  const entry: QueuedPromptEntry = {
    id: nextId(),
    text: payload.text,
    attachments: cloneAttachments(payload.attachments),
    queuedAt: Date.now()
  }

  writeBucket(bucket, [...queueFor(bucket), entry])

  return entry
}

export const dequeueQueuedPrompt = (key: string | null | undefined): null | QueuedPromptEntry => {
  const bucket = keyOf(key)

  if (!bucket) {
    return null
  }

  const [head, ...rest] = queueFor(bucket)

  if (!head) {
    return null
  }

  writeBucket(bucket, rest)

  return head
}

export const removeQueuedPrompt = (key: string | null | undefined, id: string): boolean => {
  const bucket = keyOf(key)

  if (!bucket) {
    return false
  }

  const queue = queueFor(bucket)
  const next = queue.filter(e => e.id !== id)

  if (next.length === queue.length) {
    return false
  }

  writeBucket(bucket, next)

  return true
}

/**
 * Promote an entry to the head of its bucket (the user asked for it to go
 * first). The bucket is the Agent's scope key — it never changes, so the entry
 * stays where it was queued.
 */
export const promoteQueuedPrompt = (key: string | null | undefined, id: string): boolean => {
  const bucket = keyOf(key)

  if (!bucket) {
    return false
  }

  const queue = queueFor(bucket)
  const index = queue.findIndex(e => e.id === id)

  if (index <= 0) {
    return false
  }

  const entry = queue[index]!
  writeBucket(bucket, [entry, ...queue.slice(0, index), ...queue.slice(index + 1)])

  return true
}

export const updateQueuedPrompt = (
  key: string | null | undefined,
  id: string,
  update: { text: string; attachments?: ComposerAttachment[] }
): boolean => {
  const bucket = keyOf(key)

  if (!bucket) {
    return false
  }

  const queue = queueFor(bucket)
  let changed = false

  const next = queue.map(entry => {
    if (entry.id !== id) {
      return entry
    }

    const attachments = update.attachments ? cloneAttachments(update.attachments) : entry.attachments

    if (entry.text === update.text && !update.attachments) {
      return entry
    }

    changed = true

    return { ...entry, text: update.text, attachments }
  })

  if (!changed) {
    return false
  }

  writeBucket(bucket, next)

  return true
}

export const updateQueuedPromptText = (key: string | null | undefined, id: string, text: string): boolean =>
  updateQueuedPrompt(key, id, { text })

export const clearQueuedPrompts = (key: string | null | undefined) => {
  const bucket = keyOf(key)

  if (!bucket || !(bucket in $queuedPromptsByAgent.get())) {
    return
  }

  writeBucket(bucket, [])
}

/** Inputs to {@link shouldAutoDrain}. */
export interface AutoDrainInput {
  isBusy: boolean
  queueLength: number
}

/**
 * Decide whether the composer should auto-drain the next queued prompt.
 *
 * Edge-independent on purpose: the queue must advance whenever the session is
 * idle and has pending entries, NOT only on an observed busy true → false edge.
 * A backend bounce / websocket reconnect remounts the composer and resets the
 * busy ref to the current value, swallowing the settle edge — an edge-gated
 * drain would then strand the entry forever. The caller's drain lock
 * (`drainingQueueRef`) serializes sends so being edge-free can't double-submit.
 */
export const shouldAutoDrain = ({ isBusy, queueLength }: AutoDrainInput): boolean => !isBusy && queueLength > 0

/** Auto-drain attempts for one entry before we stop retrying and toast. The
 * entry stays queued for a manual send; a remount/reconnect resets the count. */
export const MAX_AUTO_DRAIN_ATTEMPTS = 4
