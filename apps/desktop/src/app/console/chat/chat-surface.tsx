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
import { useEffect, useMemo, useRef } from 'react'

import { showsTransition } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { ChatSwapOverlay } from '@/app/chat/chat-swap-overlay'
import { ChatBar } from '@/app/chat/composer'
import { prefersReducedMotion, threadSwapAnimation } from '@/app/chat/thread-swap'
import { $consoleAgents } from '@/app/console/store/agents'
import { Thread } from '@/components/assistant-ui/thread'
import { useI18n } from '@/i18n'
import type { ChatMessage } from '@/lib/chat-messages'
import { createToolMergeCache, toBranchableMessageRepository } from '@/lib/chat-runtime'
import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import { cn } from '@/lib/utils'
import { $gatewaySwapTarget } from '@/store/profile'
import { $busy, $messages, $messagesEmpty } from '@/store/session'

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
  const { t } = useI18n()
  const messagesEmpty = useStore($messagesEmpty)
  const busy = useStore($busy)
  const enter = useStore($agentEnter)
  const gatewaySwapTarget = useStore($gatewaySwapTarget)
  const agents = useStore($consoleAgents)

  // 切换 Agent 时给新内容一次淡入上移（2026-10-21）。**这一面才是用户看的
  // 那一面**（`ChatView` 是另一套壳）——上一版把它加在 `ChatView` 上，Agent
  // 视图里一点都看不到。用 Web Animations API：不动 React 树、不重挂转录。
  const surfaceRef = useRef<HTMLDivElement>(null)
  const scopeKey = chatScopeKey(scope)
  const firstScopeRef = useRef(scopeKey)

  useEffect(() => {
    if (firstScopeRef.current === scopeKey) {
      return
    }

    firstScopeRef.current = scopeKey

    const surface = surfaceRef.current
    const animation = threadSwapAnimation(prefersReducedMotion())

    if (!surface || !animation || typeof surface.animate !== 'function') {
      return
    }

    surface.animate(animation.keyframes, animation.options)
  }, [scopeKey])

  // 空态：一个还没有对话的 Agent 不该是一片纯白（用户原话「什么东西都没有了」）。
  // 只在**既没内容、也不在忙、也不在进入中**时显示——加载态由遮罩负责，两者不叠。
  const agentName =
    scope.kind === 'agent'
      ? (agents.find(agent => agent.id === scope.id)?.name || scope.id || '').trim()
      : ''

  const showEmpty =
    messagesEmpty && !busy && !showsTransition(enter) && !gatewaySwapTarget

  return (
    <div
      className={cn('relative isolate flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)', className)}
      data-chat-scope={scopeKey}
      ref={surfaceRef}
    >
      <ChatRuntimeBoundary cancel={cancel} submit={submit}>
        {/* 高度约束这一层是必须的：`Thread` 需要一个 `flex-1 min-h-0` 的滚动容器，
            否则内容一长（或输入框长高）整块会被撑开、刚发的那条被顶出视口
            ——「发完消息 Plobi 那条被从屏幕里顶掉」就是这个形状。 */}
        <div className="relative min-h-0 flex-1 overflow-hidden">
          <Thread clampToComposer />
          {showEmpty && (
            <div
              className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 px-8 text-center"
              data-testid="chat-surface-empty"
            >
              {agentName ? (
                <p className="text-sm font-medium text-foreground">{agentName}</p>
              ) : null}
              <p className="text-xs text-(--ui-text-tertiary)">{t.console.chatEmptyHint}</p>
            </div>
          )}
        </div>
        <ScopeChatContext cancel={cancel} composer={composer} modelMenuContent={modelMenuContent} submit={submit}>
          <ChatBar />
        </ScopeChatContext>
      </ChatRuntimeBoundary>
      {/* 进入/切换那一段的过渡节点（带 Agent 名与转圈）。它原来只在 `ChatView`
          里挂着，Agent 视图因此「点下去到出内容」之间没有任何态。 */}
      <ChatSwapOverlay profile={gatewaySwapTarget} />
    </div>
  )
}
