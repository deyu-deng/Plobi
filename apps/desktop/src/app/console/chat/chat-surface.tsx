/**
 * ChatSurface — the console shell's ONE conversation component (spec §3.6).
 *
 * §3.6 forbids a second chat implementation: S1's center (`./console-chat`) and
 * S2's center (`../agent/agent-chat`) are both thin instantiations of THIS
 * component differing only by `scope`. Everything conversation-shaped lives
 * here and nowhere else:
 * - transcript     → the base `Thread` (`@/components/assistant-ui/thread`)
 * - runtime        → `useIncrementalExternalStoreRuntime` + `toRuntimeMessage`
 *                    + `ExportedMessageRepository` (base adapters)
 * - composer      → the base `ChatBar` (the SAME composer the original chat
 *                    uses), wired to the BASE session store via `ScopeChatContext`
 *                    (`./scope-context`) through the shared `ChatContext` seam
 * - message cards  → dispatched by the base `message-parts.tsx`, so the
 *                    pending-event card renders identically for L1 and L2
 *
 * Per ARCH-RULINGS 2026-09-02 the conversation is the base session store
 * (`$messages` / `$busy`), and `submit` / `cancel` are injected by the shell
 * (the real `submitText` / `cancelRun` from the desktop controller) — there is
 * no console-local store and no mock `POST /api/chat` (裁定 2).
 *
 * `scope` identity MUST stay stable for a given conversation: wrappers memoize
 * it on the agent id (or pass the `L1_SCOPE` constant). The center pane stays
 * mounted across L1 ↔ L2; the live session store swaps the transcript.
 */

import { AssistantRuntimeProvider, type ThreadMessage } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useMemo, useRef } from 'react'

import { ChatBar } from '@/app/chat/composer'
import { Thread } from '@/components/assistant-ui/thread'
import type { ChatMessage } from '@/lib/chat-messages'
import { createToolMergeCache, toBranchableMessageRepository } from '@/lib/chat-runtime'
import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import { cn } from '@/lib/utils'
import { $busy, $messages } from '@/store/session'

import { type ChatScope, chatScopeKey } from './scope'
import { type CancelFn, ScopeChatContext, type ScopeComposerActions, type SubmitFn } from './scope-context'

/**
 * Owns the store subscription and the assistant-ui runtime — same parent-chain
 * as the base `ChatRuntimeBoundary` (`toBranchableMessageRepository`). Do not
 * flatten every row to `parentId: null`; that hides the whole thread except the
 * last assistant reply.
 */
function ChatRuntimeBoundary({
  children,
  cancel,
  submit
}: {
  children: React.ReactNode
  submit?: SubmitFn
  cancel?: CancelFn
}) {
  const messages = useStore($messages)
  const busy = useStore($busy)
  const runtimeMessageCacheRef = useRef(new WeakMap<ChatMessage, ThreadMessage>())
  const toolMergeCacheRef = useRef(createToolMergeCache())

  const runtimeMessageRepository = useMemo(
    () =>
      toBranchableMessageRepository(messages, {
        runtimeMessageCache: runtimeMessageCacheRef.current,
        toolMergeCache: toolMergeCacheRef.current
      }),
    [messages]
  )

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    isRunning: busy,
    messageRepository: runtimeMessageRepository,
    onCancel: async () => {
      cancel?.()
    },
    onNew: async () => {
      // Submission is owned by the composer below, like the base ChatBar.
    },
    // Console chat deliberately does not support edit/reload yet — the base
    // session's branch/restore flow is not wired into the console shell.
    // Without these callbacks the UI hides the edit/reload affordances
    // (capabilities are gated on `onEdit !== undefined` / `onReload !== undefined`).
    // Wire them when the console gains a real session-branch RPC.
    onEdit: undefined,
    onReload: undefined
  })

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

export interface ChatSurfaceProps {
  className?: string
  scope: ChatScope
  /** Live model.options dropdown content for the composer pill. */
  modelMenuContent?: React.ReactNode
  /** Injected by the desktop controller — the real submitText path. */
  submit?: SubmitFn
  /** Injected by the desktop controller — the real cancelRun path. */
  cancel?: CancelFn
  /**
   * Injected by the desktop controller — its single `useComposerActions`
   * instance, so the composer's attach surface (files / folders / images /
   * clipboard image / drops) is wired instead of greyed out.
   */
  composer?: ScopeComposerActions
}

export function ChatSurface({
  className,
  scope,
  submit,
  cancel,
  composer,
  modelMenuContent
}: ChatSurfaceProps): React.ReactElement {
  return (
    <div
      className={cn('relative isolate flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)', className)}
      data-chat-scope={chatScopeKey(scope)}
    >
      <ChatRuntimeBoundary cancel={cancel} submit={submit}>
        <Thread clampToComposer />
        <ScopeChatContext cancel={cancel} composer={composer} modelMenuContent={modelMenuContent} submit={submit}>
          <ChatBar />
        </ScopeChatContext>
      </ChatRuntimeBoundary>
    </div>
  )
}
