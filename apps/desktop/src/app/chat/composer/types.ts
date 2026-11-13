import type { ReactNode } from 'react'

import type { PlobiGateway } from '@/plobi'
import type { ComposerAttachment } from '@/store/composer'

import type { DroppedFile } from '../hooks/use-composer-actions'

export interface ContextSuggestion {
  text: string
  display: string
  meta?: string
}

export interface QuickModelOption {
  provider: string
  providerName: string
  model: string
}

export interface ChatBarState {
  model: {
    model: string
    provider: string
    canSwitch: boolean
    loading?: boolean
    quickModels?: QuickModelOption[]
    /** Reused status-bar dropdown (built with gateway + selectModel upstream). */
    modelMenuContent?: ReactNode
  }
  tools: { enabled: boolean; label: string; suggestions?: ContextSuggestion[] }
  voice: { enabled: boolean; active: boolean }
}

/**
 * Why a user-initiated send did (not) go out.
 *
 * - `true` — it went out.
 * - `false` — the **gateway** rejected it. The error surface already spoke
 *   (an error bubble in the transcript + a toast), so the composer just puts
 *   the words back in the box.
 * - `'local'` — a **local** refusal: the session was running a turn (the busy
 *   gate), the previous submit still held the per-session lock, or the user
 *   switched conversations mid-flight. Nothing was said on screen, and these
 *   used to be indistinguishable from `false` — so the composer treated them
 *   as "the gateway said no" and put the words back in the box: the message
 *   vanished with no sound at all (user 11-13: 「输入后，发送不了了」).
 *   Now the composer **queues** it, under the conversation it was typed in:
 *   the user sees it in the queue panel and it goes out when that
 *   conversation frees up.
 *
 * Note the queue key always exists now (a conversation that has no session id
 * yet falls back to a per-Agent placeholder bucket — see
 * `store/composer-queue.ts`), so a local refusal always has somewhere to put
 * the words.
 */
export type SubmitOutcome = boolean | 'local'

export interface ChatBarProps {
  busy: boolean
  disabled: boolean
  focusKey?: string | null
  maxRecordingSeconds?: number
  state: ChatBarState
  gateway?: PlobiGateway | null
  sessionId?: string | null
  cwd?: string | null
  onCancel: () => Promise<void> | void
  onAddContextRef?: (refText: string, label?: string, detail?: string) => void
  onAddUrl?: (url: string) => void
  onAttachImageBlob?: (blob: Blob) => Promise<boolean | void> | boolean | void
  onAttachDroppedItems?: (candidates: DroppedFile[]) => Promise<boolean | void> | boolean | void
  onPasteClipboardImage?: (opts?: { silent?: boolean }) => Promise<boolean> | void
  onPickFiles?: () => void
  onPickFolders?: () => void
  onPickImages?: () => void
  onRemoveAttachment?: (id: string) => void
  onSteer?: (text: string) => Promise<boolean> | boolean
  onSubmit: (
    value: string,
    options?: {
      attachments?: ComposerAttachment[]
      fromQueue?: boolean
      /**
       * 发送锁的桶＝**Agent 的作用域键**（`l1` / `agent:<id>`）——不是会话 id：
       * 一个 Agent 一张嘴，两个 Agent 各自一把锁（以前新对话共用一把全局锁
       * `__pending_new__`，互相顶掉）。
       */
      scopeKey?: string
      /** 这一条走「转向」当前这一轮（`session.steer`），不是发一轮新的。 */
      steer?: boolean
    }
  ) => Promise<SubmitOutcome> | SubmitOutcome
  onTranscribeAudio?: (audio: Blob) => Promise<string>
}

export type VoiceStatus = 'idle' | 'recording' | 'transcribing'

export interface VoiceActivityState {
  elapsedSeconds: number
  level: number
  status: VoiceStatus
}
