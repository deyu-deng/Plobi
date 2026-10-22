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
      <div className={cn('h-full min-h-0 overflow-y-auto pb-20', PAGE_INSET_X)}>{children}</div>
    </section>
  )
}

export function Pill({ tone = 'muted', children }: { tone?: 'muted' | 'primary'; children: ReactNode }) {
  return <Badge variant={tone === 'primary' ? 'default' : 'muted'}>{children}</Badge>
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
  description,
  hint,
  action,
  below,
  onActivate,
  expanded,
  wide = false
}: {
  title: ReactNode
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
    <div className="text-[length:var(--conversation-text-font-size)] font-medium text-foreground">{title}</div>
  )

  return (
    // Container-queried, not viewport-queried: the label/control split keys on
    // the row's own pane width, so a narrow detail column (messaging, split
    // views) stacks instead of squishing the label against minmax(15rem,…).
    <div className="@container">
      <div
        className={cn(
          'grid gap-3 py-3',
          !wide && '@2xl:grid-cols-[minmax(0,1fr)_minmax(15rem,22rem)] @2xl:items-center'
        )}
      >
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
            <div className="mt-1 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)">
              {description}
            </div>
          )}
          {hint && <div className="mt-1 block font-mono text-[0.68rem] text-muted-foreground/45">{hint}</div>}
          {below}
        </div>
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
