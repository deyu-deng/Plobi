import type { FC } from 'react'

import { cn } from '@/lib/utils'

/**
 * 屏上所有「消息」共用的一层壳（裁定 196/201）。
 *
 * 之前四种消息各写各的外壳：同一把宽度尺 `min(86%,44rem)`、同一个圆角、同一档
 * 描边、同一组内边距被**手抄了三遍**，于是每改一次「统一」都要改三处、还总会漏一处。
 * 这里只留一份：气泡表面、行距、头像、署名行、系统事件那一行。
 */

/** 一张气泡最宽多少——所有消息同一把尺。 */
export const MESSAGE_MAX_WIDTH = 'max-w-[min(86%,44rem)]'

/** 气泡表面：圆角 / 描边 / 内边距 / 字号与行高。底色由各自那一档决定。 */
export const MESSAGE_BUBBLE_SURFACE = cn(
  'rounded-xl border border-border/65 px-3 py-2 text-left',
  'text-[length:var(--conversation-text-font-size)] leading-(--dt-line-height) text-foreground/95',
)

/** 主人自己的消息（右、强调色、不署名）。 */
export const MESSAGE_OWNER_BUBBLE = cn(MESSAGE_BUBBLE_SURFACE, 'bg-(--dt-user-bubble)')

/** 别人发来的消息（右、中性色）。 */
export const MESSAGE_EXTERNAL_BUBBLE = cn(MESSAGE_BUBBLE_SURFACE, 'bg-(--ui-chat-surface-background)')

/** 一行消息的外壳：靠右、与头像同一档间距。 */
export const MESSAGE_ROW = 'flex w-full items-start justify-end gap-2 py-1'

/** 头像圈：名册 icon/color 优先，没有就名字首字。 */
export const MESSAGE_AVATAR = cn(
  'mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-(--ui-stroke-tertiary)',
  'bg-(--ui-chat-surface-background) text-[10px] font-medium text-(--ui-text-secondary)',
)

/** 气泡上方那行小字（署名 / 代问）。 */
export const MESSAGE_BYLINE = 'px-1 pb-0.5 text-[10px] leading-none text-(--ui-text-tertiary)'

/** 系统事件那一行：居中灰字，无气泡无头像。 */
export const MESSAGE_SYSTEM_LINE = cn(
  MESSAGE_MAX_WIDTH,
  'px-2 py-0.5 text-center text-[0.6875rem] leading-5 text-muted-foreground/60',
)

export const MessageByline: FC<{ label: string; title?: string }> = ({ label, title }) => (
  <div className={MESSAGE_BYLINE} data-slot="aui_message-byline" title={title}>
    {label}
  </div>
)

export const MessageAvatar: FC<{ label: string; icon?: string; accent?: string; children?: never }> = ({
  label,
  icon,
  accent,
}) => (
  <div
    aria-hidden
    className={MESSAGE_AVATAR}
    style={accent ? { borderColor: accent, color: accent } : undefined}
    title={label}
  >
    {icon ? <i aria-hidden className={cn('codicon', `codicon-${icon}`)} style={{ fontSize: '0.75rem' }} /> : label.trim().slice(0, 1).toUpperCase()}
  </div>
)
