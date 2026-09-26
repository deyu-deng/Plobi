import type { ReadableAtom, WritableAtom } from 'nanostores'
import { createContext, useContext } from 'react'

import type { PlobiGateway } from '@/plobi'
import type { ChatMessage } from '@/lib/chat-messages'
import type { ComposerAttachment } from '@/store/composer'

import type { ChatBarState } from './composer/types'
import type { DroppedFile } from './hooks/use-composer-actions'

/**
 * ChatContext — the one seam that lets the SAME ChatBar render for two
 * different back-ends without a virtual-session bridge or a second composer.
 *
 * - `SessionChatContext` (./session-context) wires it to the full session store.
 * - `ScopeChatContext` (../console/chat/scope-context) wires it to the console's
 *   scope-keyed store, whose `submit` posts `POST /api/chat` with `target`.
 *
 * Everything the composer's hooks used to read straight off `@/store/session`
 * (`$messages`, `$activeSessionAwaitingInput`) or `@/store/composer`
 * (`$composerAttachments`) is now supplied here, so neither back-end leaks into
 * the composer. The atoms are passed by reference (not subscribed here) so the
 * per-token `$messages` flush keeps its existing render isolation.
 */
export interface ChatContextValue {
  /** Live transcript for THIS conversation (history browse + voice + TTS). */
  messages: ReadableAtom<ChatMessage[]>
  /** True while the conversation is blocked on the user (clarify/approval). */
  awaitingInput: boolean
  /** Composer attachments — a writable atom so draft load/stash can set it. */
  attachments: WritableAtom<ComposerAttachment[]>

  // --- the composer's submit/cancel/steer surface ---------------------------
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
  ) => Promise<boolean> | boolean
  onTranscribeAudio?: (audio: Blob) => Promise<string>
}

export const ChatContext = createContext<ChatContextValue | null>(null)

export function useChatContext(): ChatContextValue {
  const context = useContext(ChatContext)

  if (!context) {
    throw new Error('useChatContext must be used within a ChatContext provider')
  }

  return context
}
