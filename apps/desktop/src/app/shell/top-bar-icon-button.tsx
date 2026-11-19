import type { ComponentProps } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

/**
 * 对话卡抬头右上角那一族图标按钮的**唯一形态**（档案／待放行／写脑待批都走它）。
 *
 * 用户 11-19 16:0x：「**现在三个按钮一人一个样。关键是都很丑。**」
 * ⇒ 这一刀把它的形状也按 Qoder 的规格表统一：28px 方块、圆角、**零描边**、
 * 三级字色、hover 出**淡底**（Qoder 的 `hover:bg-fill-tertiary`）而不是深底。
 */
export const TOP_BAR_BUTTON =
  'grid size-7 shrink-0 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-row-hover-background) hover:text-(--ui-text-secondary)'

type Props = Omit<ComponentProps<'button'>, 'children'> & {
  count?: number
  icon: string
  label: string
  pressed?: boolean
}

export function TopBarIconButton({ className, count, icon, label, pressed, ...rest }: Props) {
  return (
    <Button
      {...rest}
      aria-label={label}
      aria-pressed={pressed}
      className={cn(TOP_BAR_BUTTON, 'relative', className)}
      data-top-bar-icon=""
      size="icon-xs"
      title={label}
      type="button"
      variant="ghost"
    >
      <Codicon name={icon} size="0.8125rem" />
      {count && count > 0 ? (
        <span className="absolute -top-0.5 -right-0.5 grid min-w-3 place-items-center rounded-full bg-(--ui-accent) px-0.5 text-[0.5rem] leading-3 text-(--ui-text-primary)">
          {count > 9 ? '9+' : count}
        </span>
      ) : null}
    </Button>
  )
}
