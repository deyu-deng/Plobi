import { ActionBarPrimitive, BranchPickerPrimitive, MessagePrimitive, useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { type FC, type ReactNode, useCallback, useRef, useState } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { DirectiveContent } from '@/components/assistant-ui/directive-text'
import { messageAttachmentRefs, messageContentText } from '@/components/assistant-ui/thread/content'
import { QuoteButton } from '@/components/assistant-ui/thread/quote-button'
import { type RestoreMessageTarget } from '@/components/assistant-ui/thread/types'
import { UserMessageText } from '@/components/assistant-ui/thread/user-message-text'
import { Codicon } from '@/components/ui/codicon'
import { CopyButton } from '@/components/ui/copy-button'
import { useResizeObserver } from '@/hooks/use-resize-observer'
import { useI18n } from '@/i18n'
import { askedByBadge, externalSenderOf, splitFilePointers, splitSpeakerPrefix } from '@/lib/chat-messages'
import { triggerHaptic } from '@/lib/haptics'
import { StopFilled } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { notifyThreadEditOpen } from '@/store/thread-scroll'
import { isWatchWindow } from '@/store/windows'

/** 一张气泡最宽多少——所有消息同一把尺。 */
const MESSAGE_MAX_WIDTH = 'max-w-[min(86%,44rem)]'

/** 气泡表面：圆角 / 描边 / 内边距 / 字号行高。底色按哪一档决定。 */
const MESSAGE_SURFACE =
  'rounded-xl border border-border/65 px-3 py-2 text-left text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground/95'

/** 主人自己的消息（右、强调色、不署名）。 */
const OWNER_BUBBLE = cn(MESSAGE_SURFACE, 'bg-(--dt-user-bubble)')

/** 别人发来的消息（右、中性色）。 */
const EXTERNAL_BUBBLE = cn(MESSAGE_SURFACE, 'bg-(--ui-chat-surface-background)')

const MESSAGE_AVATAR =
  'mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) text-[10px] font-medium text-(--ui-text-secondary)'

const MESSAGE_BYLINE = 'px-1 pb-0.5 text-[10px] leading-none text-(--ui-text-tertiary)'

const MESSAGE_SYSTEM_LINE = cn(
  MESSAGE_MAX_WIDTH,
  'px-2 py-0.5 text-center text-[0.6875rem] leading-5 text-muted-foreground/60',
)

export function StickyHumanMessageContainer({
  attachments,
  children,
  messageId
}: {
  attachments?: ReactNode
  children: ReactNode
  messageId?: string
}) {
  return (
    // Fragment, not a wrapper: a wrapping element becomes the sticky's
    // containing block (it'd stick within its own height = never). The bubble
    // and attachments are flow siblings so the bubble pins against the scroller
    // while attachments below it scroll away.
    <>
      <div
        className="group/user-message sticky z-40 -mx-4 flex w-[calc(100%+2rem)] min-w-0 max-w-none flex-col items-stretch gap-0 self-end overflow-visible bg-(--ui-chat-surface-background) px-4 pb-(--conversation-turn-gap) pt-1"
        data-message-id={messageId}
        data-role="user"
        data-slot="aui_user-message-root"
      >
        {children}
      </div>
      {attachments}
    </>
  )
}

// Shared "user bubble" base. Both the read-only message and the inline
// edit composer render the same bubble surface (rounded glass card);
// they only differ in border weight, cursor, and padding-right (the
// read-only view reserves room for the restore icon).
//
// no-drag: sticky bubbles park at --sticky-human-top (~4px), sliding under the
// titlebar's [-webkit-app-region:drag] strips (app-shell.tsx). Electron resolves
// drag regions at the compositor level — z-index and pointer-events don't help —
// so without the carve-out, clicking a stuck bubble drags the window instead of
// opening the edit composer.
export const USER_BUBBLE_BASE_CLASS =
  cn(
    'composer-human-message standalone-glass relative flex w-full min-w-0 max-w-full flex-col gap-1.5 overflow-y-auto [-webkit-app-region:no-drag]',
    OWNER_BUBBLE,
  )

export const USER_ACTION_ICON_BUTTON_CLASS =
  'grid place-items-center rounded-md bg-transparent text-(--ui-text-secondary) transition-colors hover:bg-(--ui-control-active-background) hover:text-foreground disabled:cursor-default disabled:text-(--ui-text-quaternary) disabled:opacity-70'

export const USER_ACTION_ICON_SIZE = '0.6875rem'
export const StopGlyph = <StopFilled aria-hidden className="size-3.5 -translate-y-px" />

// Background-process notifications are injected into the conversation as user
// messages (the agent must react to them, and message-role alternation forbids
// a synthetic system row mid-loop). They are NOT something the human typed, so
// render them as a compact system-style notice instead of a user bubble.
// Shape: see tools/process_registry.py format_process_notification().
const PROCESS_NOTIFICATION_RE = /^\[IMPORTANT: Background process [\s\S]*\]$/

const ProcessNotificationNote: FC<{ text: string }> = ({ text }) => {
  const body = text.replace(/^\[IMPORTANT:\s*/, '').replace(/\]$/, '')
  const newline = body.indexOf('\n')
  const headline = (newline === -1 ? body : body.slice(0, newline)).trim()
  const detail = newline === -1 ? '' : body.slice(newline + 1).trim()

  return (
    <div className="flex max-w-[min(86%,44rem)] flex-col gap-0.5 self-center px-2 py-0.5 text-[0.6875rem] leading-5 text-muted-foreground/60">
      <span className="flex items-center gap-1.5">
        <Codicon className="shrink-0 text-muted-foreground/55" name="terminal" size="0.75rem" />
        <span className="wrap-anywhere">{headline}</span>
      </span>
      {detail && (
        <details className="pl-[1.3125rem]">
          <summary className="cursor-pointer select-none text-muted-foreground/45 hover:text-muted-foreground/70">
            output
          </summary>
          <pre
            className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[0.625rem] leading-4 text-muted-foreground/55"
            data-selectable-text="true"
          >
            {detail}
          </pre>
        </details>
      )}
    </div>
  )
}

export const UserMessage: FC<{
  onCancel?: () => Promise<void> | void
  onRequestRestoreConfirm?: (messageId: string, target: RestoreMessageTarget) => void
}> = ({ onCancel, onRequestRestoreConfirm }) => {
  const { t } = useI18n()
  const copy = t.assistant.thread
  const agents = useStore($consoleAgents)
  const messageId = useAuiState(s => s.message.id)
  const content = useAuiState(s => s.message.content)
  const messageText = messageContentText(content)
  const threadRunning = useAuiState(s => s.thread.isRunning)
  // The runtime declares this, not the primitive: `ActionBarPrimitive.Edit`
  // only disables on `composer.isEditing` and `beginEdit()` throws SYNCHRONOUSLY
  // when the adapter has no `onEdit` — an uncaught error that tears the whole
  // transcript down mid-view.
  const canEdit = useAuiState(s => s.thread.capabilities.edit)

  const latestUserId = useAuiState(s => {
    for (let i = s.thread.messages.length - 1; i >= 0; i--) {
      const message = s.thread.messages[i] as { id?: string; role?: string }

      if (message.role === 'user') {
        return message.id ?? null
      }
    }

    return null
  })

  const runtimeUserOrdinal = useAuiState(s => {
    let ordinal = 0

    for (const message of s.thread.messages) {
      if (message.role !== 'user') {
        continue
      }

      if (message.id === s.message.id) {
        return ordinal
      }

      ordinal += 1
    }

    return null
  })

  const attachmentRefs = useAuiState(s => {
    const custom = (s.message.metadata?.custom ?? {}) as { attachmentRefs?: unknown }

    return messageAttachmentRefs(custom.attachmentRefs)
  })

  // Sticky human bubbles clamp to ~2 lines with a soft fade so a long prompt
  // doesn't dominate the viewport while the response streams underneath; the
  // clamp lifts on hover / focus (see styles.css). We measure the *unclamped*
  // inner wrapper so the ResizeObserver only fires on real content / width
  // changes, not on every frame while the outer max-height animates open.
  const clampInnerRef = useRef<HTMLDivElement | null>(null)
  const [bodyClamped, setBodyClamped] = useState(false)
  const lastClampHeightRef = useRef(-1)
  const lineHeightRef = useRef(0)

  // Watch windows spectate a subagent run driven elsewhere — prompts can't be
  // edited, restored, or stopped from here. The bubble stays a button that
  // toggles the 2-line clamp so long prompts are still fully readable.
  // 发起方徽标（WP-L2-OUTSOURCE-ENTRY）：值从 `messages.asked_by` 经 metadata.custom
  // 一路带上来 —— 点进一个分身，要分得开哪条是主人自己打的、哪条是秘书代他问的。
  const customMeta = useAuiState(
    s => (s.message.metadata?.custom ?? {}) as { askedBy?: unknown; kind?: unknown }
  )

  const askedBy = askedByBadge(customMeta.askedBy)
  // 自己的消息**不写名字**（微信也不在自己的气泡上署名）；只有「总秘书代问」这类
  // 第三方发起的才在气泡上方标一行，说明这不是主人亲手打的。
  const showByline = Boolean(askedBy && (askedBy.kind === 'secretary' || askedBy.kind === 'other'))

  const askedByLabel = askedBy === null
    ? ''
    : askedBy.kind === 'me'
      ? copy.askedByMe
      : askedBy.kind === 'secretary'
        ? copy.askedBySecretary
        : askedBy.kind === 'unrecorded'
          ? copy.askedByUnrecorded
          : askedBy.raw

  const readOnly = isWatchWindow()
  const [expanded, setExpanded] = useState(false)
  const clampActive = !(readOnly && expanded)

  const measureClamp = useCallback((entries: readonly ResizeObserverEntry[]) => {
    const inner = clampInnerRef.current
    const outer = inner?.parentElement

    if (!inner || !outer) {
      return
    }

    // Prefer the size the ResizeObserver already computed — reading
    // `scrollHeight` outside RO timing forces a synchronous layout, and with
    // many user bubbles observed at once those reads interleave with the
    // style write below into a read-write-read reflow cascade.
    const entryHeight = entries.find(entry => entry.target === inner)?.borderBoxSize?.[0]?.blockSize
    const fullHeight = Math.ceil(entryHeight ?? inner.scrollHeight)

    if (fullHeight === lastClampHeightRef.current) {
      return
    }

    lastClampHeightRef.current = fullHeight

    // Line-height is stable for the life of the bubble (font settings don't
    // change under it) — resolve the computed style once.
    if (!lineHeightRef.current) {
      const styles = getComputedStyle(inner)
      lineHeightRef.current = parseFloat(styles.lineHeight) || 1.5 * parseFloat(styles.fontSize) || 20
    }

    outer.style.setProperty('--human-msg-full', `${fullHeight}px`)
    setBodyClamped(fullHeight > lineHeightRef.current * 2 + 1)
  }, [])

  useResizeObserver(measureClamp, clampInnerRef)

  // Injected background-process notification, not a human prompt — render the
  // compact system-style notice (after all hooks above have run).
  if (PROCESS_NOTIFICATION_RE.test(messageText.trim())) {
    return (
      <MessagePrimitive.Root
        className="flex w-full min-w-0 flex-col items-stretch"
        data-role="user"
        data-slot="aui_user-message-root"
      >
        <ProcessNotificationNote text={messageText.trim()} />
      </MessagePrimitive.Root>
    )
  }

  const hasBody = messageText.trim().length > 0
  const isLatestUser = messageId === latestUserId
  const showStop = !readOnly && isLatestUser && threadRunning && Boolean(onCancel)
  // Restore (re-run this exact prompt) is available everywhere the Stop button
  // isn't — including mid-stream on older prompts, since the action interrupts
  // the live turn before rewinding.
  // 裁定 201：外面来的话（别的 Agent / cron 结果）不是「人」的气泡——左侧、带头像
  // 和名字；那段 `[来自 X · 时间]` 只在存储里留（模型靠它认出谁在说话），屏上不显。
  const externalSpeaker = externalSenderOf(customMeta.kind, customMeta.askedBy, messageText)

  if (externalSpeaker) {
    const split = splitSpeakerPrefix(messageText)
    const speaker = split.speaker || externalSpeaker.name
    const label = speaker || (externalSpeaker.kind === 'cron' ? '定时任务' : 'Agent')
    const quoted = (customMeta as { quoted?: { text: string } | null }).quoted ?? null

    if (externalSpeaker.kind === 'system') {
      // 系统事件：居中灰字、无气泡无头像（裁定 196 ③）。
      return (
        <div
          className="flex w-full justify-center py-1"
          data-kind="system"
          data-message-id={messageId}
          data-role="user"
          data-slot="aui_external-system"
        >
          <div className={MESSAGE_SYSTEM_LINE}>
            <UserMessageText className="wrap-anywhere" text={split.body} />
          </div>
        </div>
      )
    }

    const roster = externalSpeaker.kind === 'agent'
      ? agents.find(agent => agent.name.trim().toLowerCase() === speaker.trim().toLowerCase())
      : undefined

    const accent = roster?.color || undefined

    return (
      <div
        className="group/external flex w-full items-start justify-end gap-2 py-1"
        data-kind={externalSpeaker.kind}
        data-message-id={messageId}
        data-role="user"
        data-slot="aui_external-message"
      >
        <div className="mt-px flex shrink-0 flex-col items-center gap-0.5">
          <QuoteButton
            className="opacity-0 group-hover/external:opacity-100"
            id={messageId}
            text={quoted?.text ?? split.body}
          />
        </div>
        <div className={cn('flex min-w-0 flex-col items-end', MESSAGE_MAX_WIDTH)}>
          <div className={MESSAGE_BYLINE} data-slot="aui_message-byline" title={label}>
            {label}
          </div>
          <div className={EXTERNAL_BUBBLE}>
            {quoted ? (
              <div className="mb-1 border-l-2 border-(--ui-stroke-tertiary) pl-2 text-[0.75rem] leading-5 text-(--ui-text-tertiary)">
                <span className="line-clamp-2">{quoted.text}</span>
              </div>
            ) : null}
            {splitFilePointers(split.body).map((segment, index) =>
              segment.kind === 'file' ? (
                <div
                  className="mt-1 flex items-center gap-1.5 rounded-md border border-border/65 bg-(--ui-chat-surface-background) px-2 py-1 text-[0.75rem] text-(--ui-text-secondary)"
                  key={`file-${index}`}
                  title={segment.path}
                >
                  <Codicon name="file" size="0.75rem" />
                  <span className="truncate font-mono">{segment.path}</span>
                </div>
              ) : (
                <UserMessageText className="wrap-anywhere" key={`text-${index}`} text={segment.text} />
              ),
            )}
          </div>
        </div>
        <div
          aria-hidden
          className={MESSAGE_AVATAR}
          style={accent ? { borderColor: accent, color: accent } : undefined}
          title={label}
        >
          {externalSpeaker.kind === 'cron' ? (
            <Codicon name="clock" size="0.75rem" />
          ) : roster?.icon ? (
            <Codicon name={roster.icon} size="0.75rem" />
          ) : (
            label.trim().slice(0, 1).toUpperCase()
          )}
        </div>
      </div>
    )
  }

  const showRestore = !readOnly && !showStop && Boolean(onRequestRestoreConfirm) && hasBody

  const bubbleClassName = cn(
    USER_BUBBLE_BASE_CLASS,
    // 与外部气泡同一条宽度行为：内容多长就多宽（上限 86%/44rem），不撑满整行。
    // 原来右侧留 9 是给压在气泡里的停止/回到此处让位——动作搬到气泡下方后不再需要。
    'w-auto max-w-full cursor-pointer text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground/95 transition-colors',
    'border-(--ui-stroke-tertiary) hover:border-(--ui-stroke-secondary)'
  )

  const bubbleContent = hasBody && (
    // Render the user's text through a minimal markdown pipeline:
    // backtick `code` and ``` fenced ``` blocks, with directive chips
    // (`@file:` etc.) still resolved inside the plain-text spans.
    <div
      className={cn(clampActive && 'sticky-human-clamp')}
      data-clamped={clampActive && bodyClamped ? 'true' : undefined}
    >
      {/* Match the edit composer's collapsed line box (min-h-[1.25rem]) so
          clicking to edit can't grow the bubble by a sub-pixel and reflow the
          turn 1px. */}
      <div className="min-h-[1.25rem]" ref={clampInnerRef}>
        <UserMessageText className="wrap-anywhere" text={messageText} />
      </div>
    </div>
  )

  return (
    <MessagePrimitive.Root asChild>
      <StickyHumanMessageContainer
        attachments={
          // Attachments live BELOW the sticky bubble in normal flow, so they
          // scroll away behind the pinned bubble instead of riding along with
          // it. Image refs render as thumbnails, file refs as chips; no border.
          attachmentRefs.length > 0 ? (
            <div className="flex flex-wrap gap-1 -mt-3 mb-2">
              <DirectiveContent text={attachmentRefs.join(' ')} />
            </div>
          ) : null
        }
        messageId={messageId}
      >
        <ActionBarPrimitive.Root
          // 裁定 201：**外面来的都在右**——主人自己打的也属于「外面」，靠右并收窄；
          // 这屏 Agent 自己的回答留在左侧（纯文本）。宽度上限与外部气泡同一把尺。
          className={`relative ml-auto flex w-full ${MESSAGE_MAX_WIDTH} items-start justify-end gap-2 self-end`}
          data-slot="aui_user-bubble-actions"
        >
          <div className="human-message-with-todos-wrapper flex min-w-0 flex-1 flex-col gap-0">
            {/* 抬头那一行只放"谁发起的"；动作不再压在气泡上（用户 11-03：
                「引用符号不要和气泡重合，悬浮时下方出现编辑/复制/引用，不悬浮就隐藏」）。 */}
            {showByline && (
              <div className="flex items-center gap-1">
                <span
                  className={MESSAGE_BYLINE}
                  data-slot="aui_user-asked-by"
                  title={askedBy?.raw || undefined}
                >
                  {askedByLabel}
                </span>
              </div>
            )}
            <div className="relative flex w-full justify-end">
              {readOnly || !canEdit ? (
                // Spectator transcript, and any runtime without an `onEdit` (the
                // console shell submits through its own injected `submit`, so its
                // adapter declares no edit): clicking only toggles the clamp so the
                // full prompt is readable — it never opens an edit composer.
                <button
                  aria-expanded={bodyClamped ? expanded : undefined}
                  className={cn(bubbleClassName, !bodyClamped && 'cursor-default')}
                  onClick={() => {
                    if (!bodyClamped) {
                      return
                    }

                    triggerHaptic('selection')
                    setExpanded(value => !value)
                  }}
                  title={bodyClamped ? (expanded ? t.common.collapse : copy.expandMessage) : undefined}
                  type="button"
                >
                  {bubbleContent}
                </button>
              ) : (
                // Always editable — clicking opens the edit composer even while a
                // turn streams; sending the edit reverts (interrupt + rewind).
                <ActionBarPrimitive.Edit asChild>
                  <button
                    aria-label={copy.editMessage}
                    className={bubbleClassName}
                    onClick={() => triggerHaptic('selection')}
                    onPointerDown={() => notifyThreadEditOpen()}
                    title={copy.editMessage}
                    type="button"
                  >
                    {bubbleContent}
                  </button>
                </ActionBarPrimitive.Edit>
              )}
              {/* 自己的消息也署名（用户 11-03：「用户的也要」）。这一笔先上头像——一枚中性
                  人形、不带品牌色；名字那一格要一个「我」字的新文案，而五份语言目录此刻压在
                  别人手上，所以名字随下一个能签 i18n 的窗口补，不硬塞一句现成的长话。 */}
              <div aria-hidden className={cn(MESSAGE_AVATAR, 'self-start')} data-slot="aui_user-avatar">
                <Codicon name="person" size="0.75rem" />
              </div>
            </div>

            {/* 动作条改摆在气泡**下方**，平时不显示、悬浮或键盘聚焦到这一条才出来
                  （用户 11-03：「悬浮在气泡旁边时下方出现编辑、复制和引用等符号」）。
                  形状与 Agent 那侧的 `AssistantActionBar` 同一份：`opacity-0` +
                  `pointer-events-none`，靠 `group-hover` / `group-focus-within` 抬起来。
                  原来那两颗停止/回到此处是绝对定位压在气泡右下角的，正是要拆掉的"重合"。 */}
              <div
                className="flex items-center justify-end gap-2 py-1.5 opacity-0 pointer-events-none transition-opacity group-hover/user-message:pointer-events-auto group-hover/user-message:opacity-100 group-focus-within/user-message:pointer-events-auto group-focus-within/user-message:opacity-100"
                data-slot="aui_user-message-actions"
              >
                {showStop ? (
                  <button
                    aria-label={copy.stop}
                    className={cn('size-5', USER_ACTION_ICON_BUTTON_CLASS)}
                    onClick={event => {
                      event.preventDefault()
                      event.stopPropagation()
                      void onCancel?.()
                    }}
                    title={copy.stop}
                    type="button"
                  >
                    {StopGlyph}
                  </button>
                ) : null}
                {showRestore ? (
                  <button
                    aria-label={copy.restoreCheckpoint}
                    className={cn('size-6', USER_ACTION_ICON_BUTTON_CLASS)}
                    onClick={event => {
                      event.preventDefault()
                      event.stopPropagation()
                      triggerHaptic('selection')
                      onRequestRestoreConfirm?.(messageId, {
                        text: messageText,
                        userOrdinal: runtimeUserOrdinal
                      })
                    }}
                    onPointerDown={event => {
                      event.preventDefault()
                      event.stopPropagation()
                    }}
                    title={copy.restoreFromHere}
                    type="button"
                  >
                    <Codicon name="discard" size="0.875rem" />
                  </button>
                ) : null}
                {hasBody && !readOnly ? (
                  <CopyButton
                    appearance="icon"
                    buttonSize="icon"
                    label={copy.copy}
                    text={messageText}
                  />
                ) : null}
                <QuoteButton id={messageId} text={messageText} />
              </div>
            <BranchPickerPrimitive.Root
              className={cn(
                'checkpoint-container flex items-center gap-1 pb-0 pt-1 pl-1.5 text-[0.75rem] leading-none text-(--ui-text-tertiary)',
                readOnly && 'hidden'
              )}
              hideWhenSingleBranch
            >
              <span aria-hidden className="checkpoint-icon size-1.5 rounded-full border border-current" />
              <BranchPickerPrimitive.Previous
                className="checkpoint-restore-text rounded-sm bg-transparent px-1 opacity-65 hover:opacity-100 disabled:hidden disabled:cursor-default"
                title={copy.restorePrevious}
              >
                {copy.restoreCheckpoint}
              </BranchPickerPrimitive.Previous>
              <span className="checkpoint-divider opacity-55">
                <BranchPickerPrimitive.Number />/<BranchPickerPrimitive.Count />
              </span>
              <BranchPickerPrimitive.Next
                className="checkpoint-restore-text rounded-sm bg-transparent px-1 opacity-65 hover:opacity-100 disabled:hidden disabled:cursor-default"
                title={copy.restoreNext}
              >
                {copy.goForward}
              </BranchPickerPrimitive.Next>
            </BranchPickerPrimitive.Root>
          </div>
        </ActionBarPrimitive.Root>
      </StickyHumanMessageContainer>
    </MessagePrimitive.Root>
  )
}
