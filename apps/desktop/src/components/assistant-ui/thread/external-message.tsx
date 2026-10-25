import { useStore } from '@nanostores/react'
import type { FC } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

import { UserMessageText } from './user-message-text'

/**
 * 「外面来的」那一条：你、别的 Agent、定时任务的结果（裁定 201）。
 *
 * 用户 10-24 定的侧向：**外面来的都在右，这屏 Agent 自己的回答在左**。
 * 头像用名册里已有的 `icon`/`color`（与左栏同一套），没有就退名字首字；
 * 定时任务统一一枚闹钟，不再按作业名取字——作业名仍写在气泡上方。
 */
export const ExternalMessage: FC<{ kind: 'agent' | 'cron'; speaker: string; text: string }> = ({
  kind,
  speaker,
  text
}) => {
  const agents = useStore($consoleAgents)
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
          <UserMessageText className="wrap-anywhere" text={text} />
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
