import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * 对话卡右上角那一族（档案 / 待放行 / 写脑待批）的**唯一容器**。
 *
 * 用户 11-19 16:0x：「**我的重做UI不止是档案。是把对话卡右上角的东西的容器都统一用一个组件。
 * 就是我说的Qoder任务监控那样的。现在三个按钮一人一个样。关键是都很丑。**」
 *
 * **这一档把 Qoder 的「规格表」落成组件**（逐条实据与类名见
 * `Docs/tasks/qoder-monitor-spec-task.md` §2，来源是 Qoder 产物里的类名，不是印象）：
 *
 * | 部位 | Qoder 实据 | 本组件 |
 * | --- | --- | --- |
 * | 列宽 | `w-[280px]` | `MONITOR_WIDTH = 280` |
 * | 行 | `flex min-h-7 w-full items-center gap-2 rounded-md border-0 bg-transparent px-1 text-left !text-xs leading-4` ＋ hover `bg-fill-tertiary` | `MonitorRow` 逐字照抄（token 换成我们的 `--ui-row-hover-background`） |
 * | 图标 | `flex size-6 squircle-lg bg-fill-secondary text-text-secondary`，内 svg `size-3` | `MonitorIcon`：24px 方形底染 ＋ 内 svg 12px |
 * | 两栏 | label `text-text-secondary` ＋ value `text-text-tertiary`（`ml-auto` 右对齐） | `MonitorRow` 的 label / value |
 * | 分区标题 | `text-xs text-text-quaternary` | `MonitorSection` 的标题（**没有常驻折角**） |
 * | 间距 | 分区 `gap-4`、条目 `gap-1.5` | 容器 `gap-4`、段内 `gap-1.5` |
 * | 面板 | `flex max-h-[560px] min-h-0 flex-col` ＋ `scroll-fade-y` | `MONITOR_MAX_HEIGHT = 560` ＋ 上下渐隐遮罩 |
 * | 浮层 | `fixed z-[1000] w-[280px] rounded-lg border-border-tertiary bg-bg-container shadow-lg` | `MonitorPanel mode="float"` |
 * | 固定列 | `relative h-full min-h-0 shrink-0 overflow-hidden`（**aside 本体无 border**） | `MonitorPanel mode="column"`（**无描边**） |
 *
 * **为什么零描边**：Qoder 好看不是「没有边框」，是**所有零件同一把尺** ——
 * 靠三级字色 ＋ 6/16px 间距 ＋ hover 才出底色分层。描边一律不要。
 *
 * **新屏先找既成形状**：这一族以前有三套语言（档案走 `rail-kit`、待放行走 Popover、
 * 写脑待批又是个 Dialog），这一刀把它们收到这里；内容只写分区与行，壳不动。
 */

/** 列宽（Qoder `w-[280px]`）。 */
export const MONITOR_WIDTH = 280
/** 浮层最大高（Qoder `max-h-[560px]`）。 */
export const MONITOR_MAX_HEIGHT = 560
/** 浮层与窗口的最小间距（Qoder `MARGIN = 16`）。 */
export const MONITOR_MARGIN = 16
/** 拖动时至少留这么多可见（Qoder `MIN_VISIBLE_HEIGHT = 96`）。 */
export const MONITOR_MIN_VISIBLE = 96

/**
 * 浮层壳（Qoder：`fixed z-[1000] w-[280px] max-w-[calc(100vw-16px)] rounded-lg border border-border-tertiary bg-bg-container shadow-lg`）。
 *
 * **定位不在这里**：`fixed`（相对视口）与 `absolute`（贴着对话卡）是两种用法，
 * 由使用方通过 `className` 给 —— 这一件只管**长相**（圆角、那道淡边框、投影、280px 宽）。
 *
 * **高度上限也在这里**（Qoder `max-h-[560px]`）：这一族所有浮层的上限是**同一个数**，
 * 不许每处自己写一个（写脑待批那个模态也用它那个 `MONITOR_MAX_HEIGHT`）。
 */
export const MONITOR_FLOAT_SHAPE = cn(
  'z-[1000] flex max-h-[560px] w-[280px] max-w-[calc(100vw-16px)] flex-col overflow-hidden rounded-lg',
  'border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) shadow-lg'
)

/** 固定列（Qoder：`relative h-full min-h-0 shrink-0 overflow-hidden` —— **本体无 border**）。 */
export const MONITOR_COLUMN_SHAPE = 'relative flex h-full min-h-0 w-[280px] shrink-0 flex-col overflow-hidden'

/** 头（Qoder：`flex h-9 shrink-0 items-center gap-2 px-3`）。 */
const HEADER = 'flex h-9 shrink-0 items-center gap-2 px-3'

/** 图标托（Qoder：`flex size-6 shrink-0 items-center justify-center squircle-lg bg-fill-secondary text-text-secondary`，内 svg `size-3`）。 */
const ICON_CONTAINER =
  'flex size-6 shrink-0 items-center justify-center rounded-md bg-(--ui-bg-secondary) text-(--ui-text-secondary) [&>svg]:size-3 [&>svg]:shrink-0'

/** 标题（Qoder：`text-xs text-text-quaternary` —— 12px、不大写、无字距）。 */
const SECTION_TITLE = 'text-xs font-normal normal-case tracking-normal text-(--ui-text-quaternary)'

/** 容器体：分区之间 16px（Qoder `gap-4`），到底上下渐隐（Qoder `scroll-fade-y scroll-fade-4`）。 */
const BODY = cn(
  'flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-3',
  '[mask-image:linear-gradient(to_bottom,transparent,black_10px,black_calc(100%-10px),transparent)]'
)

export type MonitorPanelMode = 'column' | 'float'

/**
 * 一个容器：固定列（占位兄弟列）或浮层（可拖、记位置）。
 *
 * `header` 是头的内容；`headerDragProps` 挂给**拖把手**（不是整个头）——
 * 头里还有按钮，落在按钮上不该启动拖动。
 */
export function MonitorPanel({
  bodyClassName,
  children,
  className,
  header,
  headerDragProps,
  mode = 'column',
  style,
  testId,
  topRow
}: {
  bodyClassName?: string
  children?: ReactNode
  className?: string
  header?: ReactNode
  headerDragProps?: Record<string, unknown>
  mode?: MonitorPanelMode
  style?: React.CSSProperties
  /** 根元素的 `data-testid`（拖动定位那类按名字找本体的用法要它）。 */
  testId?: string
  /** 头**上面**那一行（对话卡的顶栏那一行）。并排列用它把顶栏那道线接过去。 */
  topRow?: ReactNode
}) {
  return (
    <div
      className={cn(mode === 'float' ? MONITOR_FLOAT_SHAPE : MONITOR_COLUMN_SHAPE, className)}
      data-monitor-mode={mode}
      data-monitor-panel=""
      data-testid={testId}
      style={style}
    >
      {topRow}
      {header ? (
        <div className={HEADER} data-monitor-header="">
          {header}
        </div>
      ) : null}
      <div className={cn(BODY, bodyClassName)} data-monitor-body="">
        {children}
      </div>
    </div>
  )
}

/**
 * 顶栏那一行 —— **并排列不占它**（用户 11-19：「**这个组件的侧边栏形式，不应该占据顶栏。
 * 对话卡片的顶栏应该还是不变的。**」）。
 *
 * 这一列是对话卡的**兄弟列**，跟对话区一样从卡片顶端开始 ⇒ 顶栏那一行会被它占掉。
 * 口径是「顶栏不变」⇒ 这一列从顶栏**下一行**开始，并把顶栏那道线**接过去**
 * （同一条 `border-b`、同一色）—— 于是顶栏看着横跨整卡。
 *
 * 底色用 `--ui-chat-surface-background`：那是顶栏背后那一层的颜色 ⇒ 接缝看不出来。
 */
export function MonitorTopBarRow({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'h-(--card-header-height) shrink-0 border-b border-(--ui-stroke-tertiary)',
        'bg-(--ui-chat-surface-background)',
        className
      )}
      data-monitor-topbar-row=""
    />
  )
}

/** 头里那把拖把手（Qoder：`cursor-grab active:cursor-grabbing`）。 */
export function MonitorDragHandle({
  children,
  className,
  ...rest
}: {
  children?: ReactNode
  className?: string
} & Record<string, unknown>) {
  return (
    <span
      className={cn('flex cursor-grab items-center gap-2 active:cursor-grabbing', className)}
      data-monitor-drag-handle=""
      {...rest}
    >
      {children}
    </span>
  )
}

/** 头里的标题（Qoder 的头也是 12px 一档，不放大字）。 */
export function MonitorHeaderTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn('min-w-0 truncate text-xs text-(--ui-text-secondary)', className)} data-monitor-title="">
      {children}
    </span>
  )
}

/**
 * 一个分区：标题 ＋ 内容。
 *
 * 标题**没有常驻折角**（Qoder 的分区标题就是一行字）—— 折叠状态用**右侧那颗状态点**表达
 * （Qoder 的 `size-1.5 rounded-full bg-info`），点标题本身才切换。
 */
export function MonitorSection({
  children,
  className,
  collapsed = false,
  count,
  onToggle,
  title,
  trailing
}: {
  children?: ReactNode
  className?: string
  collapsed?: boolean
  count?: number
  onToggle?: () => void
  title: ReactNode
  trailing?: ReactNode
}) {
  return (
    <section className={cn('flex min-w-0 flex-col gap-1.5', className)} data-monitor-section="">
      <div className="flex min-w-0 items-center gap-1.5">
        {onToggle ? (
          <button
            aria-expanded={!collapsed}
            className="flex min-w-0 items-center gap-1.5 text-left"
            data-monitor-section-toggle=""
            onClick={onToggle}
            type="button"
          >
            <span className={SECTION_TITLE}>{title}</span>
          </button>
        ) : (
          <span className={SECTION_TITLE}>{title}</span>
        )}
        {typeof count === 'number' && count > 0 ? (
          <span className="text-xs tabular-nums text-(--ui-text-quaternary)">{count}</span>
        ) : null}
        {trailing ? <span className="ml-auto flex shrink-0 items-center gap-1">{trailing}</span> : null}
        {collapsed ? (
          <span
            aria-hidden
            className={cn('size-1.5 shrink-0 rounded-full bg-(--ui-accent)', trailing && 'ml-auto')}
            data-monitor-collapsed-dot=""
          />
        ) : null}
      </div>
      {collapsed ? null : <div className="flex min-w-0 flex-col gap-1.5">{children}</div>}
    </section>
  )
}

/** 图标托：24px 方形底染，内 svg 12px（Qoder `TASK_MONITOR_ICON_CONTAINER_CLASS`）。 */
export function MonitorIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn(ICON_CONTAINER, className)} data-monitor-icon="">
      {children}
    </span>
  )
}

/**
 * **那一张卡的头**（用户 11-19 17:0x：这一族只留一张卡，头也是一份）。
 *
 * 头的四件东西：图标托 ＋ 标题 ＋ 角标那个数 ＋ 尾部动作（那一枚图钉）。
 *
 * **拖把手只裹住「图标 ＋ 标题」** —— 尾部的按钮自己要点得动，不该启动拖动
 * （Qoder 的浮层头 `h-9 cursor-grab`，图钉那一枚是另一个按钮）。
 *
 * **卡头里那枚矢量图标不再给了**（用户 11-19 22:0x：「**卡片里就不要显示哪个对应的
 * 矢量图标了，显示"档案"两个字就行了**」）⇒ `icon` 现在没人传；留着这个位是给
 * 别的用法留的（行里的图标托走的是 `MonitorIcon`，不是它）。
 */
export function MonitorCardHeader({
  count,
  dragProps,
  icon,
  title,
  trailing
}: {
  count?: number
  dragProps?: Record<string, unknown>
  icon?: ReactNode
  title: ReactNode
  trailing?: ReactNode
}) {
  const identity = (
    <>
      {icon ? <MonitorIcon>{icon}</MonitorIcon> : null}
      <MonitorHeaderTitle>{title}</MonitorHeaderTitle>
    </>
  )

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2" data-monitor-card-header="">
      {dragProps ? (
        <MonitorDragHandle className="min-w-0 flex-1" {...dragProps}>
          {identity}
        </MonitorDragHandle>
      ) : (
        <span className="flex min-w-0 flex-1 items-center gap-2">{identity}</span>
      )}
      {typeof count === 'number' && count > 0 ? (
        <span className="shrink-0 text-xs tabular-nums text-(--ui-text-tertiary)">{count}</span>
      ) : null}
      {trailing ? <span className="ml-auto flex shrink-0 items-center gap-1">{trailing}</span> : null}
    </div>
  )
}

/**
 * 一行：Qoder 的行是真身 —— 28px 高、6px 间距、圆角、**零描边**、hover 才出底色。
 *
 * `dense`（固定列那种窄形态）：Qoder 在固定形态下只留图标 ＋ 值，label 收进 tooltip
 * （`displayMode === "fixed"`）—— 这里用 `title` 兜住那个 tooltip。
 */
export function MonitorRow({
  actions,
  ariaPressed,
  children,
  className,
  dense = false,
  disabled = false,
  icon,
  label,
  onClick,
  title,
  value
}: {
  actions?: ReactNode
  /** 可点那一行的按下态（档案里的任务行「勾没勾」就靠它说）。 */
  ariaPressed?: boolean
  children?: ReactNode
  className?: string
  dense?: boolean
  /** 动作在飞的时候别让人再点一次（旧 `RailRow` 就有这一条，不能丢）。 */
  disabled?: boolean
  icon?: ReactNode
  label?: ReactNode
  onClick?: () => void
  /** 整行的悬浮说明（固定列那种窄形态的 label 收在这里）。 */
  title?: string
  value?: ReactNode
}) {
  const content = (
    <>
      {icon ? <MonitorIcon>{icon}</MonitorIcon> : null}
      {label !== undefined && label !== null ? (
        <span
          className={cn('min-w-0 truncate text-xs leading-4 text-(--ui-text-secondary)', dense && 'sr-only')}
          title={typeof label === 'string' ? label : undefined}
        >
          {label}
        </span>
      ) : null}
      {value !== undefined && value !== null ? (
        <span className="ml-auto min-w-0 truncate text-xs leading-4 text-(--ui-text-tertiary)">{value}</span>
      ) : null}
      {children}
      {actions ? <span className="ml-auto flex shrink-0 items-center gap-0.5">{actions}</span> : null}
    </>
  )

  const rowClass = cn(
    'flex min-h-7 w-full items-center gap-2 rounded-md border-0 bg-transparent px-1 text-left text-xs leading-4',
    'hover:bg-(--ui-row-hover-background)',
    disabled && 'opacity-50',
    className
  )

  if (onClick) {
    return (
      <button
        aria-pressed={ariaPressed}
        className={rowClass}
        data-monitor-row=""
        disabled={disabled}
        onClick={onClick}
        title={title}
        type="button"
      >
        {content}
      </button>
    )
  }

  return (
    <div className={rowClass} data-monitor-row="" title={title}>
      {content}
    </div>
  )
}

/** 空态 —— **一行破折号**（屏上纪律：空的那块就空着，不写「暂无…」）。 */
export function MonitorEmpty({ className }: { className?: string }) {
  return (
    <span className={cn('px-1 text-xs text-(--ui-text-quaternary)', className)} data-monitor-empty="">
      —
    </span>
  )
}
