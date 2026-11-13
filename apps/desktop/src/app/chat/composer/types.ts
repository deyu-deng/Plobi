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
 * - `'busy'` / `'locked'` — a **local** refusal: the session is running a turn
 *   (the busy gate) or the previous submit still holds the per-session lock.
 *   These used to be indistinguishable from `false`, so the composer treated
 *   them as "the gateway said no" and put the words back in the box — the
 *   message vanished with no sound at all (user 11-13: 「输入后，发送不了了」).
 *   Now the composer **queues** it: the words stay visible in the queue panel
 *   and go out when the session frees up.
 * - `'drifted'` — the user switched session/route while the submit was in
 *   flight. The words belong to the session they were typed in, so they go back
 *   to *that* session's draft — never into the one now on screen.
 */
export type SubmitOutcome = boolean | 'busy' | 'locked' | 'drifted'

export interface ChatBarProps {
  busy: boolean
  disabled: boolean
  focusKey?: string | null
  maxRecordingSeconds?: number
  state: ChatBarState
  gateway?: PlobiGateway | null
  queueSessionKey?: string | null
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
    options?: { attachments?: ComposerAttachment[]; fromQueue?: boolean }
  ) => Promise<SubmitOutcome> | SubmitOutcome
  onTranscribeAudio?: (audio: Blob) => Promise<string>
}

export type VoiceStatus = 'idle' | 'recording' | 'transcribing'

export interface VoiceActivityState {
  elapsedSeconds: number
  level: number
  status: VoiceStatus
}
