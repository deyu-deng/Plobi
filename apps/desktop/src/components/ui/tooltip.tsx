import { HoverCard as HoverCardPrimitive, Tooltip as TooltipPrimitive } from 'radix-ui'
import * as React from 'react'

import { cn } from '@/lib/utils'

function TooltipProvider({
  delayDuration = 0,
  // Tips are labels, not interactive surfaces. Hoverable content + Radix's
  // pointer-grace bridge is what leaves tips stuck open — especially over
  // Electron `-webkit-app-region: drag` chrome where pointermove never fires
  // to clear the grace area. Default off so open state tracks the trigger only.
  disableHoverableContent = true,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delayDuration={delayDuration}
      disableHoverableContent={disableHoverableContent}
      {...props}
    />
  )
}

function Tooltip({ ...props }: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

function TooltipTrigger({ ...props }: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
}

function TooltipContent({
  className,
  sideOffset = 6,
  collisionPadding = 8,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        // Transparent, width-capped wrapper. The visible chip is the inner inline
        // span so `box-decoration-break: clone` gives a marker-style background
        // that hugs EACH wrapped line (bg only on the text, ragged right — no
        // rectangular dead space). Instant, no transition (delayDuration=0).
        // pointer-events-none: the tip must never steal hover/clicks from the
        // chrome underneath (titlebar tools, adjacent tabs, etc.).
        className={cn('pointer-events-none z-[200] w-fit max-w-64 select-none', className)}
        collisionPadding={collisionPadding}
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        {...props}
      >
        {/* bg-foreground/text-background auto-inverts per theme. leading-normal
            keeps lines readable; py-1 makes the cloned line-boxes overlap just
            enough to read as one continuous fill (no gaps between lines). */}
        <span className="box-decoration-clone inline bg-foreground px-1.5 py-1 text-[11px] font-bold leading-normal text-background [font-family:Arial,sans-serif]">
          {children}
        </span>
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
}

interface TipProps extends Omit<React.ComponentProps<typeof TooltipPrimitive.Content>, 'content'> {
  label: React.ReactNode
  children: React.ReactNode
  delayDuration?: number
}

// Drop-in replacement for native `title=`: wrap any single element. Instant,
// position-aware, themed. Self-contained (carries its own Provider) so it works
// anywhere without a provider ancestor. Renders the child untouched when label
// is falsy. Open state is trigger-hover only — never sticky, never click-blocking.
function Tip({ label, children, delayDuration = 0, ...props }: TipProps) {
  if (!label) {
    return <>{children}</>
  }

  return (
    <TooltipProvider delayDuration={delayDuration} disableHoverableContent>
      <Tooltip disableHoverableContent>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent {...props}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/**
 * 长文本浮层的表面：宽 560（够放两三百字不折得太碎）、高度封顶在视口 60% 内
 * 滚动、正文可选中（能直接复制要写入的那段）。抽成常量只为一件事：让回归能
 * 断言「还是可滚、还是能选中」——有人把它改回单行标签样式时那两条要红。
 */
const LONG_TIP_SURFACE =
  'border-(--ui-stroke-tertiary) bg-popover text-popover-foreground z-[200] max-h-[min(60vh,520px)] w-[min(560px,80vw)] overflow-auto rounded-md border p-2 text-xs leading-relaxed break-words whitespace-pre-wrap shadow-md select-text outline-none'

interface LongTipProps {
  /** 全文，可带换行。为空时原样渲染 children，不挂浮层。 */
  label: React.ReactNode
  children: React.ReactNode
  align?: React.ComponentProps<typeof HoverCardPrimitive.Content>['align']
  /** 传了就受控（`undefined` 才是跟随鼠标）。 */
  open?: boolean
  side?: React.ComponentProps<typeof HoverCardPrimitive.Content>['side']
}

/**
 * 长文本悬浮（看全文用）—— 给「要写进脑的什么」这种一次几百字的正文。
 *
 * 为什么不复用上面那颗 `Tip`：它是给 11px 标签用的（`max-w-64`、`select-none`、
 * `pointer-events-none`，内层还是 `box-decoration-clone` 的高亮块）。拿它装整段
 * 正文，字会挤成一条、不能滚、也不能选中复制。
 *
 * 为什么不用原生 `title=`：Electron（Chromium）对 `title` 有长度上限，几百字只
 * 显示开头；而且延迟约 1 秒、不能滚、不能复制。这正是「内容太长，悬浮看不到
 * 全部」那个缺口 —— 换 HoverCard 之后：宽 560、封顶高度内滚动、可选中、保留换行。
 *
 * 与 `Tip` 的分工：一句话的说明走 `Tip`（快、贴边、不挡东西）；要看全文走这颗。
 */
function LongTip({ align = 'start', children, label, open, side = 'bottom' }: LongTipProps) {
  if (!label) {
    return <>{children}</>
  }

  return (
    <HoverCardPrimitive.Root closeDelay={120} open={open} openDelay={140}>
      <HoverCardPrimitive.Trigger asChild>{children}</HoverCardPrimitive.Trigger>
      <HoverCardPrimitive.Portal>
        <HoverCardPrimitive.Content
          align={align}
          className={LONG_TIP_SURFACE}
          data-slot="long-tip-content"
          side={side}
          sideOffset={6}
        >
          {label}
        </HoverCardPrimitive.Content>
      </HoverCardPrimitive.Portal>
    </HoverCardPrimitive.Root>
  )
}

export { LONG_TIP_SURFACE, LongTip, Tip, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger }
