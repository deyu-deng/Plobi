import type { ComponentProps } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

/** 对话卡抬头右上角那一族图标按钮的唯一形态：档案与「待放行」共用这一个组件。 */
export const TOP_BAR_BUTTON =
  'grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'

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
      size="icon-xs"
      title={label}
      type="button"
      variant="ghost"
    >
      <Codicon name={icon} size="0.85rem" />
      {count && count > 0 ? (
        <span className="absolute -top-0.5 -right-0.5 grid min-w-3 place-items-center rounded-full bg-(--ui-accent) px-0.5 text-[0.5rem] leading-3 text-white">
          {count > 9 ? '9+' : count}
        </span>
      ) : null}
    </Button>
  )
}
