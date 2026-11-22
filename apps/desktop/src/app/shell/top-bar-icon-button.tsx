import type { ComponentProps } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip } from '@/components/ui/tooltip'
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

/**
 * 整行入口的悬浮提示一律走 `Tip`（Radix Portal），**不用原生 `title=`**。
 *
 * 用户 11-22：「右上角三个容器里面…把鼠标悬浮在某个组件上的小浮窗会显示不全」。
 * 原因就是这三个按钮原来只挂了原生 `title=`：Chromium 的原生提示由窗口自己画，
 * 抬头正贴着窗口上边缘、这一族又贴着右边缘，浮窗溢出的部分直接被窗口边界裁掉；
 * 而同一行右侧的边控件（`CardEdgeControlButtons`）走的是 `Tip`，所以只有这三枚像被切了一刀。
 *
 * 两个细节：
 * - `side="bottom"`：抬头永远在窗口最顶上，往上弹没有空间，与其让 Radix 每次自己翻转，
 *   不如钉死朝下；`align="end"` 让浮窗右侧对齐按钮右侧，长标签往左长，不会顶出窗口。
 * - `collisionPadding` 留出与窗口边缘的余量，避免浮窗贴着边被当成溢出再挤一次。
 */
export function TopBarIconButton({ className, count, icon, label, pressed, ...rest }: Props) {
  return (
    <Tip align="end" collisionPadding={8} label={label} side="bottom">
      <Button
        {...rest}
        aria-label={label}
        aria-pressed={pressed}
        className={cn(TOP_BAR_BUTTON, 'relative', className)}
        data-top-bar-icon=""
        size="icon-xs"
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
    </Tip>
  )
}
