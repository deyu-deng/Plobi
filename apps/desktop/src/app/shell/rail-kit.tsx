import type { ComponentProps, FocusEvent, MouseEvent, ReactElement, ReactNode } from 'react'
import { cloneElement, useState } from 'react'

import { PanelSectionLabel } from '@/app/overlays/panel'
import { Codicon } from '@/components/ui/codicon'
import { RowButton } from '@/components/ui/row-button'
import { cn } from '@/lib/utils'

/**
 * 对话卡右上角那条 rail（档案 / 待放行 / 以后新增）**共用的一套壳**。
 *
 * 用户 11-14 09:4x：「**我希望以后在这个地方添加的东西除了子内容的差异性，UI/UX 共用的是同一套组件。**」
 *
 * **为什么是这一档**：11-10 用户说过上半句（「在它的图标旁边加一个图标，和档案一个形态，
 * 复用一个组件」）⇒ **入口**那一半做了（`chat-top-bar.tsx` 的两枚图标共用 `TopBarIconButton`）。
 * 但**浮层**那一半一直没人做 ⇒ 同一个地方点开的两个浮层是两套语言：待放行走
 * `overlays/panel.tsx` 的 `Panel` 家族，档案卡是手搓全家桶（自搓图标按钮／chip／行／
 * 小标题／两个浮层，字号七种）。「又做了一个图标在旁边」的病根不是那个图标，
 * 是**图标点开后的那张卡没有共同的壳**。
 *
 * 这一档把「rail 上一个可增删的小块」的形状收在一处：
 *   • `RailSection` —— 一段：小标题 ＋ 内容槽（小标题走 `PanelSectionLabel`，既成那把尺）
 *   • `RailCard`    —— 一段里的一个图标块（标题 ＋ 正文）—— 收编 `right-rail/notes-list.tsx` 那套 `RAIL_CARD`
 *   • `RailRow`     —— 一行（`RowButton` 的排版变体）
 *   • `RailEmpty`   —— 空态（走 `PanelEmpty` 的紧凑档，同一套图标语言）
 *   • `DetailPeek`  —— hover 出详情的浮层（**目标与阶段共用这一个**；外观沿用原来那张卡浮层，
 *                      不换成 Tooltip 那种 marker 小贴纸 —— 详情是两行字，贴纸装不下）
 *
 * **新增一个东西 = 只写内容，不写壳**：入口进 `chat-top-bar.tsx` 的注册表，卡里的段进 `RailSection`。
 */

/** 触发器要能接住 hover／聚焦这四个钩子（`DetailPeek` 把它们合并上去）。 */
type PeekTriggerProps = {
  onBlur?: (event: FocusEvent<HTMLElement>) => void
  onFocus?: (event: FocusEvent<HTMLElement>) => void
  onMouseEnter?: (event: MouseEvent<HTMLElement>) => void
  onMouseLeave?: (event: MouseEvent<HTMLElement>) => void
}

/** 一段：小标题 ＋ 内容槽。 */
export function RailSection({
  children,
  className,
  count,
  title,
  trailing
}: {
  children: ReactNode
  className?: string
  count?: number
  title: string
  trailing?: ReactNode
}) {
  return (
    <section className={cn('px-3 pb-3 pt-2.5', className)} data-slot="rail-section">
      <div className="flex items-center gap-1.5">
        <PanelSectionLabel>{title}</PanelSectionLabel>
        {typeof count === 'number' && count > 0 ? (
          <span className="text-[0.6rem] tabular-nums text-muted-foreground/60">{count}</span>
        ) : null}
        {trailing ? <span className="ml-auto flex items-center gap-1">{trailing}</span> : null}
      </div>
      <div className="mt-1.5">{children}</div>
    </section>
  )
}

/** 一段里的一个图标块（图标 ＋ 标题 ＋ 正文）。 */
export function RailCard({
  children,
  className,
  icon,
  title
}: {
  children?: ReactNode
  className?: string
  icon?: string
  title?: ReactNode
}) {
  return (
    <div className={cn('flex gap-1.5 rounded-md px-1.5 py-1.5 text-left', className)} data-slot="rail-card">
      {icon ? (
        <Codicon className="mt-0.5 shrink-0 text-(--ui-text-tertiary)" name={icon} size="0.8125rem" />
      ) : null}
      <div className="min-w-0">
        {title ? <p className="text-[0.72rem] font-medium text-foreground">{title}</p> : null}
        {children ? (
          <div className="text-[0.7rem] leading-relaxed text-(--ui-text-secondary)">{children}</div>
        ) : null}
      </div>
    </div>
  )
}

/** 一行：走 `RowButton`（既有），排版走这一档的尺。 */
export function RailRow({ className, ...props }: ComponentProps<typeof RowButton>) {
  return (
    <RowButton
      className={cn(
        'flex w-full items-start gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-(--ui-control-hover-background)/60',
        className
      )}
      {...props}
    />
  )
}

/**
 * 段内空态 —— **一行破折号**。
 *
 * 屏上纪律（用户 10-27 令）：「空的那块就**空着**（一行破折号），不写「暂无…」这类安慰话」。
 * 这一档把它收成一个件，免得每处各写一个破折号。
 * （整块面板的空态另说 —— 那是 `PanelEmpty`，图标 ＋ 一句话。）
 */
export function RailEmpty({ className }: { className?: string }) {
  return (
    <span className={cn('block px-1 text-[0.62rem] text-(--ui-text-tertiary)', className)} data-slot="rail-empty">
      —
    </span>
  )
}

/** 一颗可选中的小圆片（日期切换那种）。 */
export function RailChip({
  active,
  children,
  className,
  ...props
}: ComponentProps<'button'> & { active?: boolean }) {
  return (
    <button
      aria-pressed={active}
      className={cn(
        'shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem] transition-colors hover:bg-(--ui-control-hover-background)',
        active
          ? 'border-transparent bg-(--ui-control-hover-background) font-medium text-foreground'
          : 'border-(--ui-stroke-tertiary) text-(--ui-text-secondary)',
        className
      )}
      type="button"
      {...props}
    >
      {children}
    </button>
  )
}

/** 浮层的外观：沿用原来档案卡那张小卡的形（边框 ＋ 圆角 ＋ 阴影 ＋ elevated 底）。 */
const PEEK_CARD =
  'pointer-events-none absolute z-20 w-44 rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) px-2 py-1.5 text-left text-[0.65rem] leading-relaxed shadow-lg'

/**
 * hover／聚焦出详情的浮层 —— **目标与阶段共用这一个**。
 *
 * 以前是两个各自手搓的浮层（`dossier-stage-bar.tsx` 里那个 `CARD` 常量用了两处），
 * 外观一样、代码两份；以后再有一处「点开看详情」也走它。
 *
 * 触发器是**唯一子元素**，hover／聚焦的开关挂在它身上（不是挂在外面那层）——
 * 这样「鼠标停在按钮上」与「键盘 Tab 到按钮上」是同一件事，且触发器原有的
 * `onMouseEnter` 等不被顶掉。
 */
export function DetailPeek({
  children,
  className,
  content,
  placement = 'top-end',
  testId
}: {
  children: ReactElement<PeekTriggerProps>
  /** 根元素的类名 —— 触发器本来是绝对定位／撑满一行时，定位上下文得由它接过去。 */
  className?: string
  content: ReactNode
  placement?: 'top-center' | 'top-end'
  /** 浮层的 `data-testid`（既有测试按名字找那两张小卡）。 */
  testId?: string
}) {
  const [open, setOpen] = useState(false)

  const trigger = children.props
  const cloned = cloneElement(children, {
    onBlur: (event: FocusEvent<HTMLElement>) => {
      trigger.onBlur?.(event)
      setOpen(false)
    },
    onFocus: (event: FocusEvent<HTMLElement>) => {
      trigger.onFocus?.(event)
      setOpen(true)
    },
    onMouseEnter: (event: MouseEvent<HTMLElement>) => {
      trigger.onMouseEnter?.(event)
      setOpen(true)
    },
    onMouseLeave: (event: MouseEvent<HTMLElement>) => {
      trigger.onMouseLeave?.(event)
      setOpen(false)
    }
  })

  return (
    <span className={cn('relative inline-flex', className)}>
      {cloned}
      {open ? (
        <span
          className={cn(
            PEEK_CARD,
            placement === 'top-center' ? 'bottom-full left-1/2 mb-1 -translate-x-1/2' : 'bottom-full right-0 mb-1'
          )}
          data-slot="detail-peek"
          data-testid={testId}
        >
          {content}
        </span>
      ) : null}
    </span>
  )
}
