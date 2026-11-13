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

import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef } from 'react'

import { showsTransition } from '@/app/chat/agent-enter'
import { $agentEnter } from '@/app/chat/agent-enter-store'
import { ChatSwapOverlay } from '@/app/chat/chat-swap-overlay'
import { ChatBar } from '@/app/chat/composer'
import { prefersReducedMotion, threadSwapAnimation } from '@/app/chat/thread-swap'
import { $consoleAgents } from '@/app/console/store/agents'
import { secretaryShellLabel } from '@/app/desktop-controller-utils'
import { Thread } from '@/components/assistant-ui/thread'
import type { ThreadSpeaker } from '@/components/assistant-ui/thread/types'
import { PlobiWordmark } from '@/components/brand-mark'
import { useIntroGreeting } from '@/components/chat/intro'
import { useI18n } from '@/i18n'
import { useChatMessageRuntime } from '@/lib/chat-runtime'
import { cn } from '@/lib/utils'
import { $gatewaySwapTarget } from '@/store/scope'
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

  // 造运行时这件事只有一处（`@/lib/chat-runtime` 的 useChatMessageRuntime）：
  // 这一面与「外面的对话」那张卡片共用同一套缓存、仓库与增量 store。
  const runtime = useChatMessageRuntime({
    isRunning: busy,
    messages,
    onCancel: async () => {
      cancel?.()
    },
    onNew: async () => {
      // Submission is owned by the composer below, like the base ChatBar.
    }
    // Console chat deliberately does not support edit/reload yet — the base
    // session's branch/restore flow is not wired into the console shell.
    // Without these callbacks the UI hides the edit/reload affordances
    // (capabilities are gated on `onEdit !== undefined` / `onReload !== undefined`).
    // Wire them when the console gains a real session-branch RPC.
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
  const { t, locale } = useI18n()
  const messagesEmpty = useStore($messagesEmpty)
  const busy = useStore($busy)
  const enter = useStore($agentEnter)
  const gatewaySwapTarget = useStore($gatewaySwapTarget)
  const agents = useStore($consoleAgents)
  // 空态上那句招呼语：取自 `components/chat/intro-copy.jsonl` 那只文案池（`Intro`
  // 组件的池子）——随当前语言、随挂载轮换，屏上不会跳。
  const greeting = useIntroGreeting({ locale })

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

  // 每条回答的署名（用户 11-03：「消息要加在气泡上面旁边或者侧边加上 Agent 的名字和
  // 头像」）。名字与卡片抬头、窗口标题同一份推导（裁定 16 的 `secretaryShellLabel`），
  // 字形与颜色取名册那一行——与左栏那一列同一份身份，这里再造一套就会两处漂。
  const rosterRow = scope.kind === 'agent' ? agents.find(agent => agent.id === scope.id) : undefined

  // 署名对象必须**同一份**：`Thread` 用它算出消息组件的类型，身份一变 ⇒
  // 整条转录被拆掉重挂（屏上就是「一直在闪」）。所以按名字/颜色/图标三个
  // 原始值缓存，只有内容真变了才换一份新的。
  const speakerName = secretaryShellLabel(
    scope.kind === 'agent' ? { l2: scope.id } : 'l1',
    t.console.chiefSecretary,
    agents
  )

  const assistantSpeaker = useMemo<ThreadSpeaker>(
    () => ({ color: rosterRow?.color, icon: rosterRow?.icon, name: speakerName }),
    [rosterRow?.color, rosterRow?.icon, speakerName]
  )

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
          <Thread assistantSpeaker={assistantSpeaker} clampToComposer />
          {showEmpty && (
            <div
              className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 px-8 text-center"
              data-testid="chat-surface-empty"
            >
              {/* 空态：品牌几何字标（随主题色），底下是一句招呼语 ＋ 一句提示。
                  原来这里写的是 Agent 的名字——那个名字撞上产品名时，活文字看着像
                  一行没排版的品牌字样（用户 11-08：「空态现在就是一个 Plobi 的纯文字」）。
                  名字本来就在面板抬头写着，这里不重复。招呼语取自文案池
                  （`intro-copy.jsonl`），随语言、随挂载换一句。 */}
              <PlobiWordmark className="w-48" />
              <p className="text-sm text-(--ui-text-secondary)">{greeting}</p>
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
