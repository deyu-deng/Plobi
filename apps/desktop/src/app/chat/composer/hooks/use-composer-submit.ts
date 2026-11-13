import { type RefObject, useEffect, useRef } from 'react'

import { SLASH_COMMAND_RE } from '@/lib/chat-runtime'
import { triggerHaptic } from '@/lib/haptics'
import { clearSessionDraft, type ComposerAttachment } from '@/store/composer'
import { resetBrowseState } from '@/store/composer-input-history'
import { enqueueQueuedPrompt, type QueuedPromptEntry } from '@/store/composer-queue'
import { $quoteTarget, clearQuoteTarget } from '@/store/quote'

import { useChatContext } from '../../context'
import { cloneAttachments, type QueueEditState } from '../composer-utils'
import { onComposerSubmitRequest } from '../focus'
import { composerPlainText } from '../rich-editor'
import type { ChatBarProps } from '../types'

interface UseComposerSubmitArgs {
  activeQueueSessionKey: string | null
  activeQueueSessionKeyRef: RefObject<string | null>
  attachments: ComposerAttachment[]
  busy: boolean
  canSteer: boolean
  clearDraft: () => void
  disabled: boolean
  draftRef: RefObject<string>
  drainNextQueued: () => Promise<boolean>
  editorRef: RefObject<HTMLDivElement | null>
  exitQueuedEdit: (action: 'cancel' | 'save') => boolean
  focusInput: () => void
  inputDisabled: boolean
  loadIntoComposer: (text: string, attachments: ComposerAttachment[]) => void
  onCancel: ChatBarProps['onCancel']
  onSteer: ChatBarProps['onSteer']
  onSubmit: ChatBarProps['onSubmit']
  queueCurrentDraft: () => boolean
  queueEdit: QueueEditState | null
  queueText: (text: string, attachments: ComposerAttachment[]) => boolean
  queuedPrompts: QueuedPromptEntry[]
  sessionId: string | null | undefined
  setComposerText: (value: string) => void
  stashAt: (scope: string | null, text?: string, attachments?: ComposerAttachment[]) => void
}

/**
 * The composer's submit engine — the orchestration seam where the draft and
 * queue meet. `submitDraft` is the one decision tree (queue-edit save · slash-
 * now-while-busy · queue · drain · send · stop); `dispatchSubmit` is the shared
 * send primitive: it sends, and it decides what happens to the words when the
 * send does NOT go out — a local refusal (busy / locked) QUEUES them, a drift
 * (the user switched session mid-flight) puts them back into the session they
 * were typed in, a gateway rejection restores them in the box (the error
 * surface already spoke). Nothing is ever dropped in silence.
 */
export function useComposerSubmit({
  activeQueueSessionKey,
  activeQueueSessionKeyRef,
  attachments,
  busy,
  canSteer,
  clearDraft,
  disabled,
  draftRef,
  drainNextQueued,
  editorRef,
  exitQueuedEdit,
  focusInput,
  inputDisabled,
  loadIntoComposer,
  onCancel,
  onSteer,
  onSubmit,
  queueCurrentDraft,
  queueEdit,
  queueText,
  queuedPrompts,
  sessionId,
  setComposerText,
  stashAt
}: UseComposerSubmitArgs) {
  const { attachments: attachmentsAtom } = useChatContext()

  // Shared send primitive: fire onSubmit, and if the gateway rejects (accepted
  // === false) or throws, re-load + re-stash the draft so the words survive.
  const dispatchSubmit = (text: string, attachments?: ComposerAttachment[]) => {
    const submittedScope = activeQueueSessionKeyRef.current
    const submittedAttachments = attachments ?? []

    const restore = () => {
      loadIntoComposer(text, submittedAttachments)
      // Use the scope captured at dispatch, not whatever session is focused
      // now — the gateway can reject well after the user has switched away,
      // and re-stashing into the currently-focused session would overwrite
      // its draft with the rejected text from a different session (#54527).
      stashAt(submittedScope, text, submittedAttachments)
    }

    // 引用条（裁定 196 ④）：发出去时把 `reply_to` 一起带上；发成功才清掉引用，
    // 被网关拒了（restore）就留着，用户改完再发还是引着那一条。
    const quote = $quoteTarget.get()
    const options = {
      ...(attachments ? { attachments } : {}),
      ...(quote ? { replyTo: quote.id } : {}),
    }

    void Promise.resolve(Object.keys(options).length ? onSubmit(text, options) : onSubmit(text))
      .then(accepted => {
        // 本地被拒（会话在跑 / 上一笔还占着锁）：这句话**不丢**——排进队列。
        // 队列面板上看得见它，会话一闲就发出去。原来这里把它当成「网关拒了」，
        // 把草稿恢复回输入框：既没发出去、也没排队、还没有任何提示
        // （用户 11-13：「输入后。发送不了了」）。
        if (accepted === 'busy' || accepted === 'locked') {
          if (!queueText(text, submittedAttachments)) {
            // 这一场还没有队列键（连会话 id 都还没有）⇒ 只能把话放回输入框。
            restore()
          }

          return
        }

        // 提交途中用户换了会话/路由：这句话属于原来那一场 ⇒ 只回灌原来那一场的
        // 草稿；灌进当前那一场就是错投（用户换会话的半句话跑到别的对话里）。
        if (accepted === 'drifted') {
          stashAt(submittedScope, text, submittedAttachments)

          if (submittedScope === activeQueueSessionKeyRef.current) {
            loadIntoComposer(text, submittedAttachments)
          }

          return
        }

        if (accepted === false) {
          // 网关拒了：错误面（转录里的错误气泡 + 提示）已经说过话，这里只把
          // 字放回输入框，用户改完再发。
          restore()

          return
        }

        clearSessionDraft(submittedScope)
        if (quote) {
          clearQuoteTarget()
        }
      })
      .catch(restore)
  }

  // External "submit this prompt" requests (e.g. the review pane's agent-ship
  // button) route through the same send path. A ref keeps the listener stable
  // while always calling the latest dispatchSubmit closure.
  const dispatchSubmitRef = useRef(dispatchSubmit)
  dispatchSubmitRef.current = dispatchSubmit

  useEffect(
    () =>
      onComposerSubmitRequest(({ target, text }) => {
        if (target === 'main' && !inputDisabled) {
          dispatchSubmitRef.current(text)
        }
      }),
    [inputDisabled]
  )

  const submitDraft = () => {
    if (disabled) {
      return
    }

    // Source the text from the DOM editor, not React state. The AUI composer
    // state (`draft`) and the derived `hasComposerPayload` lag the DOM by a
    // render, so on fast typing or IME composition the final keystroke(s) may
    // not have synced yet — reading state here drops the message (Enter looks
    // like it does nothing; typing a trailing space only "fixes" it because the
    // extra input event forces a state sync). draftRef is updated on every
    // input event; refresh it from the editor once more to also cover an
    // in-flight keystroke that hasn't fired its input event yet.
    const editor = editorRef.current

    if (editor) {
      const domText = composerPlainText(editor)

      if (domText !== draftRef.current) {
        draftRef.current = domText
        setComposerText(domText)
      }
    }

    const text = draftRef.current
    const payloadPresent = text.trim().length > 0 || attachments.length > 0

    if (queueEdit) {
      exitQueuedEdit('save')
    } else if (busy) {
      // Slash commands should execute immediately even while the agent is
      // busy — they're client-side operations (/yolo, /skin, /new, /help,
      // etc.) or self-contained gateway RPCs (/status, /compress).  onSubmit
      // routes them to executeSlashCommand, which has its own per-command
      // busy guard for commands that genuinely need an idle session (skill
      // /send directives).  Queuing them would make every slash command wait
      // for the current turn to finish, which is how the TUI never behaves.
      if (!attachments.length && SLASH_COMMAND_RE.test(text.trim())) {
        triggerHaptic('submit')
        clearDraft()
        dispatchSubmit(text)
      } else if (payloadPresent) {
        queueCurrentDraft()
      } else {
        // Stop button (the only way to reach here while busy with an empty
        // composer — empty Enter is short-circuited in the keydown handler).
        triggerHaptic('cancel')
        void Promise.resolve(onCancel())
      }
    } else if (!payloadPresent && queuedPrompts.length > 0) {
      void drainNextQueued()
    } else if (payloadPresent) {
      const submittedAttachments = cloneAttachments(attachments)
      triggerHaptic('submit')
      resetBrowseState(sessionId)
      clearDraft()
      attachmentsAtom.set([])
      dispatchSubmit(text, submittedAttachments)
    }

    focusInput()
  }

  // Steer the live turn (nudge without interrupting). Clears the draft up front
  // for snappy feedback; if the gateway rejects (no live tool window) the words
  // are re-queued so nothing is lost — same safety net as a plain queue.
  const steerDraft = () => {
    if (!onSteer || !canSteer) {
      return
    }

    const text = draftRef.current.trim()

    triggerHaptic('submit')
    clearDraft()

    void Promise.resolve(onSteer(text)).then(accepted => {
      if (!accepted && activeQueueSessionKey) {
        enqueueQueuedPrompt(activeQueueSessionKey, {
          text,
          attachments: [],
          ...($quoteTarget.get() ? { replyTo: $quoteTarget.get()!.id } : {}),
        })
        if ($quoteTarget.get()) {
          clearQuoteTarget()
        }
      }
    })
  }

  return { dispatchSubmit, steerDraft, submitDraft }
}
