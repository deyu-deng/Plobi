import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { getVirtuosoLog, logLine } from '@/app/chat/sidebar/virtuoso-runs'
import {
  deleteExternalSession,
  getExternalTranscript,
  patchExternalSession,
  sendExternalMessage,
  vendorLabel,
  whenText
} from '@/app/chat/sidebar/external-sessions'
import { VendorBrandBlock } from '@/app/chat/sidebar/offload-vendor-icon'
import { Thread } from '@/components/assistant-ui/thread'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n'
import type { ChatMessage } from '@/lib/chat-messages'
import { useChatMessageRuntime } from '@/lib/chat-runtime'
import { closeConversation, type OpenConversation, openConversation } from '@/store/conversation-view'

import { VirtuosoModelBar } from './virtuoso-model-bar'

/**
 * 主区那张对话卡片，显示**不在这个 Agent 自己会话里**的一段对话（用户 11-01）。
 *
 * 复用与 `ChatSurface` 同一条管线：`Thread`（同一套气泡）+ `toBranchableMessageRepository`
 * + `useIncrementalExternalStoreRuntime`。区别只有数据源——这里是外部软件那条会话，
 * 或者一个临时专家自己的工作日志。**不再用弹窗**：弹窗是上一版的形状，用户点名换掉。
 *
 * 底部输入区只在「那家软件支持从这里说话」时出现；专家日志是它干完活的记录，只读。
 */

type Line = { role: 'assistant' | 'user', text: string }

export function ConversationPane({
  conversation,
  showHeader
}: {
  conversation: OpenConversation
  /** 卡片抬头（关掉卡片模式时由外层补 titlebar 高度，这里不画）。 */
  showHeader: boolean
}) {
  const { t } = useI18n()
  const c = t.console
  const [lines, setLines] = useState<Line[]>([])
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  /** 那条会话的工作目录（后端给的）：只进抬头的悬停，不上屏。 */
  const [project, setProject] = useState('')

  const { agentId, kind, sessionId } = conversation
  const vendor = conversation.kind === 'external' ? conversation.vendor : ''
  const running = conversation.kind === 'virtuoso' ? conversation.running === true : false

  const load = useCallback(
    async (cancelled: () => boolean) => {
      // 名册上那四行永远点得开，但没派过活时没有会话号——不必去读一个不存在的日志。
      if (kind === 'virtuoso' && !sessionId) {
        setLines([])
        setError('')

        return
      }

      try {
        if (kind === 'external') {
          const body = await getExternalTranscript(agentId, vendor, sessionId)

          if (!cancelled()) {
            setProject(String(body?.data?.project ?? ''))
            setLines((body?.data?.messages ?? []).map(message => ({ role: message.role, text: message.text })))
          }

          return
        }

        if (!sessionId) {
          setLines([])

          return
        }

        const body = await getVirtuosoLog(sessionId)

        if (cancelled()) {
          return
        }

        // 第一条用户消息就是任务书本身；抬头已经写着同一句话，不再在正文里占一块。
        // 标注返回类型：不标的话数组字面量会把 `'user' | 'assistant'` 拓宽成 `string`。
        setLines(
          (body?.messages ?? []).slice(1).map<Line>(message => ({
            role: message.role === 'user' ? 'user' : 'assistant',
            text: logLine(message).text
          })).filter(line => line.text)
        )
      } catch (reason) {
        if (!cancelled()) {
          setLines([])
          // 后端那句话要跟着出来：只说「读不出来」等于把判据藏了（同发送失败那条）。
          setError(
            `${kind === 'external' ? c.externalSessionsLoadFailed : c.virtuososLoadFailed}${failureText(reason)}`
          )
        }
      }
    },
    [agentId, c.virtuososLoadFailed, c.externalSessionsLoadFailed, kind, sessionId, vendor]
  )

  useEffect(() => {
    let gone = false
    const cancelled = () => gone

    setLines([])
    setError('')
    void load(cancelled)

    // 正在干的专家会自己往下走；已经干完的和外部会话不必空轮询。
    const timer = running ? setInterval(() => void load(cancelled), 2000) : undefined

    return () => {
      gone = true

      if (timer) {
        clearInterval(timer)
      }
    }
  }, [load, running])

  const messages = useMemo<ChatMessage[]>(
    () =>
      lines.map((line, index) => ({
        id: `${kind}-${index}`,
        parts: [{ text: line.text, type: 'text' }],
        role: line.role
      })),
    [kind, lines]
  )

  // 与正常对话同一套运行时（`@/lib/chat-runtime` 里那唯一一处）——区别只在数据源。
  const runtime = useChatMessageRuntime({
    isRunning: pending,
    messages,
    onCancel: async () => undefined,
    onNew: async () => undefined
  })

  const send = async (text: string): Promise<boolean> => {
    if (conversation.kind !== 'external' || !text.trim() || pending) {
      return false
    }

    setPending(true)
    setError('')
    setLines(prev => [...prev, { role: 'user', text }])

    try {
      const body = await sendExternalMessage(conversation.agentId, conversation.vendor, conversation.sessionId, text)
      setLines(prev => [...prev, { role: 'assistant', text: String(body?.data?.reply ?? '') }])

      return true
    } catch (reason) {
      setError(`${c.externalSessionsSendFailed}${failureText(reason)}`)

      return false
    } finally {
      setPending(false)
    }
  }

  // 机器细节（会话号、工作目录）只进悬停：抬头上一件也不多。
  const detail = [
    conversation.kind === 'external' ? conversation.sessionId : '',
    project
  ].filter(Boolean).join(' · ')

  return (
    <div className="relative isolate flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)">
      {showHeader && <ConversationTopBar conversation={conversation} detail={detail} />}

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <AssistantRuntimeProvider runtime={runtime}>
          <Thread clampToComposer />
        </AssistantRuntimeProvider>

        {lines.length === 0 && !error && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-8 text-center">
            {kind === 'external' ? (
              <p className="text-xs text-(--ui-text-tertiary)">{c.externalSessionsNoMessages}</p>
            ) : sessionId ? (
              <p className="text-xs text-(--ui-text-tertiary)">{c.virtuososEmpty}</p>
            ) : (
              // 这一类名手还没被派过活。原来只有一句挤在一起的长句，读起来像报错；
              // 这里统一走 EmptyState：一行短标题 + 一句说明，与别处空态同一形状。
              <EmptyState className="min-h-0" description={c.virtuososIdleHint} title={c.virtuososIdleTitle} />
            )}
          </div>
        )}
      </div>

      {error && <p className="shrink-0 px-4 pb-1 text-[0.72rem] text-(--ui-red)">{error}</p>}

      {conversation.kind === 'external' ? (
        // 能说话的那家给输入框；不能说话的那家**照实说一句**（现成那一句，四语目录里早就有）——
        // 什么都不画＝看着像这一格坏了。
        conversation.sendable ? (
          <ConversationComposer disabled={pending} onSend={send} />
        ) : (
          <ReadOnlyNotice />
        )
      ) : conversation.kind === 'virtuoso' && conversation.role ? (
        // 专家卡片底部那条：位置与高度照主对话的 composer，里面只放模型选择。
        // 选完存到这个角色上，下次派它就用这个模型（用户 11-04）。
        <div className="shrink-0 px-4 pb-[var(--composer-shell-pad-block-end,0.75rem)] pt-2">
          <div className="mx-auto flex w-full max-w-[var(--composer-width,46rem)] items-center justify-between gap-2 rounded-2xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-3 py-2">
            <span className="min-w-0 truncate text-[0.72rem] text-(--ui-text-quaternary)">{c.virtuosoModelHint}</span>
            <VirtuosoModelBar role={conversation.role} />
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ConversationTopBar({ conversation, detail }: { conversation: OpenConversation, detail: string }) {
  const { t } = useI18n()
  const c = t.console
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  /**
   * 「刚才这次改名只改在 Plobi 这边」那枚注脚（11-22 写回）。
   *
   * 它挂在**这次改名**上，不是这条会话的属性 ⇒ 换一条会话就摘掉；
   * 内容存后端那原话（「这家软件里改不了名」），鼠标停上去才展开。
   */
  const [localOnlyNote, setLocalOnlyNote] = useState('')
  /** 这次改名被后端顶回来了（软件正开着等）——原话贴出来，改名框**不关**，人改一下就能再存。 */
  const [renameError, setRenameError] = useState('')

  const external = conversation.kind === 'external' ? conversation : null

  useEffect(() => {
    setLocalOnlyNote('')
    setRenameError('')
  }, [external?.sessionId])

  const saveRename = () => {
    const title = draft.trim()

    if (!external || !title) {
      return
    }

    void patchExternalSession(external.agentId, external.vendor, external.sessionId, { title })
      .then(result => {
        // 那家软件开着、或它的文件写不进去：后端直接拒（409 一类）。这时**不许**把新名字
        // 顶到屏上——屏上叫新名、它自己那边还是老名，人回头再改一遍就撞上了。
        if (!result.ok) {
          setRenameError(String((result.data as { error?: string } | undefined)?.error || ''))

          return
        }

        setRenameError('')
        setRenaming(false)
        // 写进去了就不留标；没写进去把后端那句人话留下，标题旁边挂一枚「仅本地」——
        // 不许让人回头对着两个不一样的名字自己猜哪边算数。
        setLocalOnlyNote(result.data?.written_back === false ? String(result.data?.title_note || '') : '')
        openConversation({ ...external, title })
      })
      .catch(() => setRenameError(c.externalSessionsRenameFailed))
  }

  const toggleArchive = () => {
    if (!external) {
      return
    }

    void patchExternalSession(external.agentId, external.vendor, external.sessionId, {
      archived: true
    }).then(() => closeConversation())
  }

  const remove = () => {
    if (!external) {
      return
    }

    if (!confirmDelete) {
      setConfirmDelete(true)

      return
    }

    void deleteExternalSession(external.agentId, external.vendor, external.sessionId).then(() => closeConversation())
  }

  return (
    <div className="flex h-(--card-header-height) shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2.5">
      <Button
        aria-label={c.conversationBack}
        className="shrink-0 text-(--ui-text-tertiary) hover:text-foreground"
        onClick={() => closeConversation()}
        size="icon-xs"
        type="button"
        variant="ghost"
      >
        <Codicon name="arrow-left" size="0.875rem" />
      </Button>

      {external ? (
        // 第一格是**品牌方块**：与左栏分组行、设置屏 Providers 那一屏同一颗
        // （品牌字形优先，没有字形落中性底＋首字母）——不再拿一枚中性图标替它认领身份。
        <VendorBrandBlock size="md" vendor={external.vendor} />
      ) : (
        <span className="grid size-4 shrink-0 place-items-center text-(--ui-text-tertiary)">
          <Codicon name="person" size="0.875rem" />
        </span>
      )}

      {renaming ? (
        <>
          <input
            autoFocus
            className={`min-w-0 flex-1 rounded-md border bg-transparent px-2 py-0.5 text-[0.8125rem] text-foreground outline-none ${renameError ? 'border-(--ui-red)' : 'border-(--ui-stroke-tertiary) focus:border-(--ui-stroke-secondary)'}`}
            onChange={event => {
              setDraft(event.target.value)
              setRenameError('')
            }}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                saveRename()
              }

              if (event.key === 'Escape') {
                event.preventDefault()
                setRenaming(false)
              }
            }}
            placeholder={
              external?.titleWritable === true
                ? c.externalSessionsRenameSync
                : external?.titleWritable === false
                  ? c.externalSessionsRenameLocalOnly
                  : undefined
            }
            value={draft}
          />
          {renameError ? (
            <span className="shrink-0 text-(--ui-red)" title={renameError}>
              <Codicon name="warning" size="0.75rem" />
            </span>
          ) : null}
        </>
      ) : (
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="min-w-0 truncate text-[0.8125rem] font-medium text-foreground" title={detail}>
            {conversation.title}
          </span>
          {localOnlyNote ? (
            <span
              className="shrink-0 rounded border border-(--ui-stroke-tertiary) px-1 text-[0.62rem] leading-4 text-(--ui-text-tertiary)"
              title={localOnlyNote}
            >
              {c.externalSessionsTitleLocalOnly}
            </span>
          ) : null}
        </span>
      )}

      {external ? (
        // 谁家的、什么时候的：品牌名来自共用那张表，时间用现成那个 —— 两件都不是新文案。
        <span className="shrink-0 text-[0.68rem] text-(--ui-text-tertiary)">
          <span>{vendorLabel(external.vendor)}</span>
          {external.updatedAt ? <span>{` · ${whenText(external.updatedAt)}`}</span> : null}
        </span>
      ) : null}

      {external ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label={c.conversationActions}
              className="shrink-0 text-(--ui-text-tertiary) hover:text-foreground"
              size="icon-xs"
              type="button"
              variant="ghost"
            >
              <Codicon name="ellipsis" size="0.875rem" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => {
                setDraft(external.title)
                setRenaming(true)
              }}
            >
              <Codicon name="edit" size="0.75rem" />
              {c.externalSessionsRename}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={toggleArchive}>
              <Codicon name="archive" size="0.75rem" />
              {c.externalSessionsArchive}
            </DropdownMenuItem>
            <DropdownMenuItem
              className={confirmDelete ? 'text-(--ui-red)' : undefined}
              onSelect={remove}
            >
              <Codicon name="trash" size="0.75rem" />
              {confirmDelete ? c.externalSessionsDeleteConfirm : c.externalSessionsDelete}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </div>
  )
}

/**
 * 不能说话的那家：屏上**照实说一句**（现成那一句，四语目录里早就有，只是谁也没用过）。
 *
 * 什么都不画＝看着像这一格坏了；写「读不出来」＝那是另一回事（读得好好的）。
 * 位置：输入框本来该在的那儿——形状上说得通「这儿本来有个输入框」。
 */
function ReadOnlyNotice() {
  const { t } = useI18n()

  return (
    <div className="shrink-0 px-4 pb-[var(--composer-shell-pad-block-end,0.75rem)] pt-2">
      <div className="mx-auto w-full max-w-[var(--composer-width,46rem)] px-3 py-2 text-[0.72rem] text-(--ui-text-tertiary)">
        {t.console.externalSessionsReadOnly}
      </div>
    </div>
  )
}

function ConversationComposer({ disabled, onSend }: { disabled: boolean, onSend: (text: string) => Promise<boolean> }) {
  const { t } = useI18n()
  const c = t.console
  const [draft, setDraft] = useState('')

  const submit = () => {
    const text = draft.trim()

    if (!text || disabled) {
      return
    }

    setDraft('')
    void onSend(text)
  }

  return (
    <form
      className="shrink-0 px-4 pb-[var(--composer-shell-pad-block-end,0.75rem)] pt-2"
      onSubmit={event => {
        event.preventDefault()
        submit()
      }}
    >
      <div className="mx-auto w-full max-w-[var(--composer-width,46rem)]">
        <div className="rounded-2xl border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) p-2">
          <textarea
            className="max-h-40 min-h-9 w-full resize-none bg-transparent px-1 py-1 text-[0.8125rem] leading-snug text-foreground outline-none placeholder:text-(--ui-text-quaternary)"
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder={c.externalSessionsInput}
            rows={2}
            value={draft}
          />
          <div className="flex items-center justify-end pt-1">
            <Button
              aria-label={c.externalSessionsSend}
              disabled={disabled || !draft.trim()}
              size="icon-sm"
              type="submit"
              variant="default"
            >
              <Codicon name={disabled ? 'loading' : 'arrow-up'} size="0.875rem" spinning={disabled} />
            </Button>
          </div>
        </div>
      </div>
    </form>
  )
}

/** `fetchJson` 对非 2xx 是 reject，消息形如 `400: {...}`；把那句话取出来给人看。 */
function failureText(reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : String(reason ?? '')
  const start = raw.indexOf('{')

  if (start >= 0) {
    try {
      const parsed = JSON.parse(raw.slice(start)) as { error?: unknown }

      if (typeof parsed.error === 'string' && parsed.error) {
        return parsed.error
      }
    } catch {
      // 不是 JSON 就按原文
    }
  }

  return raw.slice(0, 160)
}
