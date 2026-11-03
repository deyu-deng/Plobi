import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

/**
 * 左栏中段的那枚**来回切换**控件（用户 11-01，参照物是他给的那张「终端 / 通用」）。
 *
 * 形状：一条胶囊轨道，选中那一格才展开成「图标 + 名词」，没选中的只留图标。
 * 这样两格的宽度会随选中项变——正是参照物的样子，读起来是"当前在哪一页"，
 * 而不是并排两个按钮。
 *
 * 语义用 `aria-pressed` 的切换按钮组，不用 tablist：这两页不是同一个 tabpanel 的两页，
 * 而是左栏中段两种内容（名单 / 外包），按钮组是更诚实的描述。
 */
export interface SidebarSegmentItem<T extends string> {
  /** Codicon 名（与左栏其它行同一套图标）。 */
  icon: string
  id: T
  /** 名词，选中时才上屏。 */
  label: string
}

export function SidebarSegment<T extends string>({
  className,
  items,
  onChange,
  value
}: {
  className?: string
  items: readonly SidebarSegmentItem<T>[]
  onChange: (id: T) => void
  value: T
}) {
  return (
    <div
      className={cn('flex w-fit shrink-0 items-center gap-0.5 rounded-full bg-(--ui-bg-secondary) p-0.5', className)}
    >
      {items.map(item => {
        const active = item.id === value

        return (
          <button
            aria-label={item.label}
            aria-pressed={active}
            className={cn(
              'flex h-6 items-center gap-1.5 rounded-full text-[0.75rem] font-medium transition-colors duration-100 ease-out',
              active
                ? 'border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) px-2.5 text-foreground'
                : 'w-6 justify-center text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)'
            )}
            key={item.id}
            onClick={() => onChange(item.id)}
            title={item.label}
            type="button"
          >
            <Codicon className="leading-none" name={item.icon} size="0.875rem" />
            {active ? <span className="truncate">{item.label}</span> : null}
          </button>
        )
      })}
    </div>
  )
}
