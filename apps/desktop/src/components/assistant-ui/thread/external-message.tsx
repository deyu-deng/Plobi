import { useStore } from '@nanostores/react'
import type { FC } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { Codicon } from '@/components/ui/codicon'
import { type QuotedPreview, splitFilePointers } from '@/lib/chat-messages'
import { cn } from '@/lib/utils'

import { UserMessageText } from './user-message-text'

/**
 * 「外面来的」那一条：你、别的 Agent、定时任务的结果（裁定 201）。
 *
 * 用户 10-24 定的侧向：**外面来的都在右，这屏 Agent 自己的回答在左**。
 * 头像用名册里已有的 `icon`/`color`（与左栏同一套），没有就退名字首字；
 * 定时任务统一一枚闹钟，不再按作业名取字——作业名仍写在气泡上方。
 */
export const ExternalMessage: FC<{
  kind: 'agent' | 'cron' | 'system'
  speaker: string
  text: string
  quoted?: null | QuotedPreview
  /** 时间轴靠它做锚点（`[data-message-id]`）——这个属性丢了，列表点了就跳不过去。 */
  messageId?: string
}> = ({ kind, speaker, text, quoted, messageId }) => {
  const agents = useStore($consoleAgents)

  if (kind === 'system') {
    // 系统发生的事：居中灰字、无气泡、无头像（裁定 196 ③）。微信里
    // 「I tickled …」那一行就是这个位置。叫醒那轮的提示词是一段脚手架，
    // 屏幕上只说「谁叫醒了它」——原文留在库里，模型读得到。
    const line = speaker && text.includes('刚给你发了一条消息') ? `${speaker} 叫醒了这个 Agent` : text

    return (
      <div
        className="flex w-full justify-center py-1"
        data-kind="system"
        data-message-id={messageId}
        data-role="user"
        data-slot="aui_external-system"
      >
        <div className="max-w-[min(86%,44rem)] px-2 py-0.5 text-center text-[0.6875rem] leading-5 text-muted-foreground/60">
          <UserMessageText className="wrap-anywhere" text={line} />
        </div>
      </div>
    )
  }

  const roster =
    kind === 'agent'
      ? agents.find(agent => agent.name.trim().toLowerCase() === speaker.trim().toLowerCase())
      : undefined

  const label = speaker || (kind === 'cron' ? '定时任务' : 'Agent')
  const letter = label.trim().slice(0, 1).toUpperCase()
  const accent = roster?.color || undefined

  return (
    <div
      className="flex w-full items-start justify-end gap-2 py-1"
      data-kind={kind}
      data-message-id={messageId}
      data-role="user"
      data-slot="aui_external-message"
    >
      <div className="flex min-w-0 max-w-[min(86%,44rem)] flex-col items-end">
        <div className="px-1 pb-0.5 text-[10px] leading-none text-(--ui-text-tertiary)">{label}</div>
        <div
          className={cn(
            'rounded-xl border border-border/65 bg-(--ui-chat-surface-background) px-3 py-2 text-left',
            'text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground/95'
          )}
        >
          {quoted ? (
            <div className="mb-1 border-l-2 border-(--ui-stroke-tertiary) pl-2 text-[0.75rem] leading-5 text-(--ui-text-tertiary)">
              <span className="line-clamp-2">{quoted.text}</span>
            </div>
          ) : null}
          {splitFilePointers(text).map((segment, index) =>
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
        className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) text-[10px] font-medium text-(--ui-text-secondary)"
        style={accent ? { borderColor: accent, color: accent } : undefined}
        title={label}
      >
        {kind === 'cron' ? (
          <Codicon name="clock" size="0.75rem" />
        ) : roster?.icon ? (
          <Codicon name={roster.icon} size="0.75rem" />
        ) : (
          letter
        )}
      </div>
    </div>
  )
}
