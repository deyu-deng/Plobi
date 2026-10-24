import { useStore } from '@nanostores/react'
import type { FC } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

import { UserMessageText } from './user-message-text'

/**
 * 「外面来的」那一条：别的 Agent 说的话、定时任务的结果（裁定 201）。
 *
 * 用户 10-23 拍的规则：**这屏的 Agent 自己说的话在右，其余一切（他、别的 Agent、
 * cron）都在左**——所以这里不是一个头像都没有的灰框，而是「谁说的 + 头像 + 气泡」。
 * 头像用名册里已有的 `icon`/`color`（与左栏同一套视觉），没有就退成名字首字。
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
    <div className="flex w-full items-start gap-2 py-1" data-kind={kind} data-slot="aui_external-message">
      <div
        aria-hidden
        className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) text-[10px] font-medium text-(--ui-text-secondary)"
        style={accent ? { borderColor: accent, color: accent } : undefined}
        title={label}
      >
        {roster?.icon ? <Codicon name={roster.icon} size="0.75rem" /> : letter}
      </div>
      <div className="min-w-0 max-w-[min(86%,44rem)]">
        <div className="px-1 pb-0.5 text-[10px] leading-none text-(--ui-text-tertiary)">{label}</div>
        <div
          className={cn(
            'rounded-xl border border-border/65 bg-(--ui-chat-surface-background) px-3 py-2',
            'text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground/95'
          )}
        >
          <UserMessageText className="wrap-anywhere" text={text} />
        </div>
      </div>
    </div>
  )
}
