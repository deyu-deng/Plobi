import type { ReactNode } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DisclosureCaret } from '@/components/ui/disclosure-caret'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { PAGE_INSET_X } from '../layout-constants'

export function SettingsContent({ children }: { children: ReactNode }) {
  return (
    <section className="min-h-0 overflow-hidden">
      {/* 正文尺收到 820px 居中（Qoder 实测 `max-w-[820px]`）：设置是一行一行的读物流，
          跟着窗口铺到 1200px 时"标题—控件"之间那条空档会宽到看不出是一对。
          别的浮层暂时还吃 `PAGE_MAX_W`，一块一块搬。 */}
      <div
        className={cn(
          'mx-auto h-full min-h-0 w-full max-w-[var(--dl-measure-page)] overflow-y-auto pb-20',
          PAGE_INSET_X
        )}
      >
        {children}
      </div>
    </section>
  )
}

export function Pill({ tone = 'muted', children }: { tone?: 'muted' | 'primary'; children: ReactNode }) {
  return <Badge variant={tone === 'primary' ? 'default' : 'muted'}>{children}</Badge>
}

/**
 * 一组一张卡（用户 11-02 拍「外壳 + 设置」，形状照 Qoder 实测搬：卡 + 44px 组抬头 +
 * 行与行之间**虚线**分隔，末行不带）。组名不再画中黑体 + 图标那一行——它是"这一堆是什么"，
 * 不是标题，所以 12px 常规体、tertiary 色。
 *
 * 卡面几何不在这里写：`.dl-card` 那一份（8px squircle + 1px 发丝边 + 同一支阴影），
 * 与浮层外壳、主窗口那两张卡同源。
 */
export function SettingsGroup({
  action,
  children,
  description,
  title
}: {
  action?: ReactNode
  children: ReactNode
  description?: ReactNode
  /** 不给组名就只剩一张卡（有些屏本来就没有第二个组名可叫）。 */
  title?: ReactNode
}) {
  const caption = 'text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)]'

  return (
    <section className="mt-5 first:mt-0">
      {(title || description || action) && (
        <header className="flex min-h-11 items-end justify-between gap-3 px-1">
          <div className="min-w-0">
            {title && <h2 className={cn('truncate font-normal text-(--ui-text-tertiary)', caption)}>{title}</h2>}
            {description && <p className={cn('mt-0.5 text-(--ui-text-tertiary)', caption)}>{description}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={cn('dl-card px-4', (title || description || action) && 'mt-2')}>
        <div className="flex flex-col [&>*+*]:border-t [&>*+*]:border-dashed [&>*+*]:border-(--ui-stroke-tertiary)">
          {children}
        </div>
      </div>
    </section>
  )
}

export function SectionHeading({ icon: Icon, title, meta }: { icon: IconComponent; title: string; meta?: string }) {
  return (
    <div className="mb-2.5 flex items-center gap-2 pt-2 text-[length:var(--conversation-text-font-size)] font-medium">
      <Icon className="size-4 text-muted-foreground" />
      <span>{title}</span>
      {meta && <Pill>{meta}</Pill>}
    </div>
  )
}

export function NavLink({
  icon: Icon,
  label,
  active,
  onClick
}: {
  icon: IconComponent
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <Button
      className={cn(
        'flex min-h-7 w-full justify-start gap-2 rounded-md px-2 text-left text-[length:var(--conversation-text-font-size)] transition',
        active
          ? 'bg-(--ui-bg-tertiary) text-foreground'
          : 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      )}
      onClick={onClick}
      size="sm"
      type="button"
      variant="ghost"
    >
      <Icon className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </Button>
  )
}

export function ListRow({
  title,
  icon,
  description,
  hint,
  action,
  below,
  onActivate,
  expanded,
  wide = false
}: {
  title: ReactNode
  /**
   * 卡片语法的第一格（`Docs/specs/settings-card-system.md` §1）：名字左边那颗图标。
   * **间距归原语，不归调用方**——`gap-2` 与 `shrink-0` 由这里保证，调用方只递节点。
   * 不传就等于不存在，其余设置页一个字不变。
   */
  icon?: ReactNode
  description?: ReactNode
  hint?: ReactNode
  action?: ReactNode
  below?: ReactNode
  /**
   * 给整行标题加"点开"这一层（`WP-SOURCE-SURFACE`：一条信源一行，行上只留一颗开关，
   * 其余信息收进点开的抽屉）。**不传就等于不存在**——其余设置页一个字不变。
   * `expanded` 只管 `aria-expanded` 与那个尖角，画法由调用方决定。
   */
  expanded?: boolean
  onActivate?: () => void
  wide?: boolean
}) {
  const heading = (
    <div className="text-[length:var(--conversation-text-font-size)] leading-[var(--dl-leading-row-title)] font-medium text-foreground">
      {title}
    </div>
  )

  const label = (
    <div className="min-w-0">
      {onActivate ? (
        <button
          aria-expanded={expanded ?? false}
          className="-mx-1 flex w-[calc(100%+0.5rem)] items-start gap-1 rounded-md px-1 text-left hover:bg-(--ui-bg-hover)"
          onClick={onActivate}
          type="button"
        >
          {heading}
          <DisclosureCaret className="mt-1 size-3.5 shrink-0 text-(--ui-text-tertiary)" open={expanded ?? false} />
        </button>
      ) : heading}
      {description && (
        <div className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-(--ui-text-tertiary)">
          {description}
        </div>
      )}
      {hint && <div className="mt-1 block font-mono text-[0.68rem] text-muted-foreground/45">{hint}</div>}
      {below}
    </div>
  )

  return (
    // Container-queried, not viewport-queried: the label/control split keys on
    // the row's own pane width, so a narrow detail column (messaging, split
    // views) stacks instead of squishing the label against the control.
    //
    // 控件那一列改成 `auto`（Qoder 实测：控件靠右、不预留一条固定宽）。以前那条
    // `minmax(15rem,22rem)` 让一颗开关后面拖着 240px 空白，一行读起来是断的。
    <div className="@container">
      <div
        className={cn(
          'grid gap-3 py-[var(--dl-row-pad-y)]',
          !wide && '@2xl:grid-cols-[minmax(0,1fr)_auto] @2xl:items-center'
        )}
      >
        {icon ? (
          <div className="flex min-w-0 items-start gap-2">
            <span className="shrink-0">{icon}</span>
            {label}
          </div>
        ) : (
          label
        )}
        {action && <div className={cn('min-w-0', !wide && '@2xl:justify-self-end')}>{action}</div>}
      </div>
    </div>
  )
}

export function LoadingState({ label }: { label: string }) {
  return <PageLoader label={label} />
}

// Canonical implementation lives in components/ui; re-exported so the many
// settings call sites keep their import path.
export { EmptyState } from '@/components/ui/empty-state'
