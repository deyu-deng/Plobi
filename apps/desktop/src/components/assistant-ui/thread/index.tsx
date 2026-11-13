import { type FC, useCallback, useMemo, useState } from 'react'

import { AssistantMessage } from '@/components/assistant-ui/thread/assistant-message'
import { ThreadMessageList } from '@/components/assistant-ui/thread/list'
import {
  BackgroundResumeNotice,
  CenteredThreadSpinner,
  ResponseLoadingIndicator
} from '@/components/assistant-ui/thread/status'
import { SystemMessage } from '@/components/assistant-ui/thread/system-message'
import { ThreadTimeline } from '@/components/assistant-ui/thread/timeline'
import { type RestoreMessageTarget, type ThreadSpeaker } from '@/components/assistant-ui/thread/types'
import { UserEditComposer } from '@/components/assistant-ui/thread/user-edit-composer'
import { UserMessage } from '@/components/assistant-ui/thread/user-message'
import { Intro, type IntroProps } from '@/components/chat/intro'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useI18n } from '@/i18n'
import type { PlobiGateway } from '@/plobi'
import { notifyError } from '@/store/notifications'

type ThreadLoadingState = 'response' | 'session'

export const Thread: FC<{
  clampToComposer?: boolean
  cwd?: string | null
  gateway?: PlobiGateway | null
  intro?: IntroProps
  loading?: ThreadLoadingState
  onBranchInNewChat?: (messageId: string) => void
  onCancel?: () => Promise<void> | void
  onDismissError?: (messageId: string) => void
  onRestoreToMessage?: (messageId: string, target?: RestoreMessageTarget) => Promise<void> | void
  sessionId?: string | null
  sessionKey?: string | null
  /** 这一面的说话人（Agent 那一侧的抬头）。不给就没有抬头那一行——Legacy `chat/index.tsx`
   *  那一壳不传，`console/chat/chat-surface.tsx` 这一面传。 */
  assistantSpeaker?: ThreadSpeaker
}> = ({
  clampToComposer = false,
  cwd = null,
  gateway = null,
  intro,
  loading,
  onBranchInNewChat,
  onCancel,
  onDismissError,
  onRestoreToMessage,
  sessionId = null,
  sessionKey,
  assistantSpeaker
}) => {
  const { t } = useI18n()
  const copy = t.assistant.thread

  const [restoreConfirmTarget, setRestoreConfirmTarget] = useState<
    (RestoreMessageTarget & { messageId: string }) | null
  >(null)

  const closeRestoreConfirm = useCallback(() => setRestoreConfirmTarget(null), [])

  const confirmRestore = useCallback(() => {
    if (!restoreConfirmTarget || !onRestoreToMessage) {
      throw new Error('Restore is unavailable for this message.')
    }

    const { messageId, text, userOrdinal } = restoreConfirmTarget

    closeRestoreConfirm()
    void Promise.resolve(onRestoreToMessage(messageId, { text, userOrdinal })).catch((error: unknown) => {
      notifyError(error, 'Restore failed')
    })
  }, [closeRestoreConfirm, onRestoreToMessage, restoreConfirmTarget])

  const requestRestoreConfirm = useCallback((messageId: string, target: RestoreMessageTarget) => {
    setRestoreConfirmTarget({ messageId, ...target })
  }, [])

  // 消息组件的**身份**必须稳。
  //
  // assistant-ui 按组件类型决定这一条是「更新」还是「挂」——身份一变，React 就把
  // 整条转录拆掉重挂。实测（开发态探针，160 条转录）：每来一次上层重渲染，
  // 80 个气泡连同 Markdown 块、复制按钮、图标全部重建（上千个 DOM 节点），
  // 一秒 2.7 次 ⇒ 屏上「一直在闪」，渲染进程常驻一个核、内存一路涨。
  //
  // 所以：会变的 prop 一律按**内容**进依赖——署名拆成名字/颜色/图标三个原始值，
  // 组件本体只在内容真变了的时候才换身份。
  const { color: speakerColor, icon: speakerIcon, name: speakerName } = assistantSpeaker ?? {}

  const speaker = useMemo<ThreadSpeaker | undefined>(
    () => (speakerName === undefined ? undefined : { color: speakerColor, icon: speakerIcon, name: speakerName }),
    [speakerColor, speakerIcon, speakerName]
  )

  const messageComponents = useMemo(
    () => ({
      AssistantMessage: () => (
        <AssistantMessage
          onBranchInNewChat={onBranchInNewChat}
          onDismissError={onDismissError}
          speaker={speaker}
        />
      ),
      SystemMessage,
      UserEditComposer: () => <UserEditComposer cwd={cwd} gateway={gateway} sessionId={sessionId} />,
      UserMessage: () => (
        <UserMessage
          onCancel={onCancel}
          onRequestRestoreConfirm={onRestoreToMessage ? requestRestoreConfirm : undefined}
        />
      )
    }),
    [speaker, cwd, gateway, onBranchInNewChat, onCancel, onDismissError, onRestoreToMessage, requestRestoreConfirm, sessionId]
  )

  // 空态与加载指示同样要**同一份元素**：它们是 `ThreadMessageList`（memo）的 prop，
  // 每次新建元素就把 memo 破掉，整条转录跟着重渲染一遍。
  const emptyPlaceholder = useMemo(
    () =>
      intro ? (
        <div className="flex min-h-0 w-full flex-col items-center justify-center pt-[var(--composer-measured-height)]">
          <Intro {...intro} />
        </div>
      ) : undefined,
    [intro]
  )

  const loadingIndicator = useMemo(
    () => (loading === 'response' ? <ResponseLoadingIndicator /> : <BackgroundResumeNotice />),
    [loading]
  )

  return (
    <div className="relative grid h-full min-h-0 max-w-full grid-rows-[minmax(0,1fr)] overflow-hidden bg-transparent contain-[layout_paint]">
      <ThreadMessageList
        clampToComposer={clampToComposer}
        components={messageComponents}
        emptyPlaceholder={emptyPlaceholder}
        loadingIndicator={loadingIndicator}
        sessionKey={sessionKey}
      />
      {loading === 'session' && <CenteredThreadSpinner />}
      <ThreadTimeline />
      <ConfirmDialog
        confirmLabel={copy.restoreConfirm}
        description={copy.restoreBody}
        destructive
        onClose={closeRestoreConfirm}
        onConfirm={confirmRestore}
        open={Boolean(restoreConfirmTarget)}
        title={copy.restoreTitle}
      />
    </div>
  )
}
