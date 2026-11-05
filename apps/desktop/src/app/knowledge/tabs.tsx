import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

/**
 * 顶部标签栏：像 Obsidian 那样同时开着几篇笔记，来回对照。
 *
 * 气质上的取舍：标签不写「文件路径」，只写**标题**；关标签是一个 × 形状的
 * 图标按钮（不是文字「关闭」）。当前标签用一条底边高亮，不用底色块——
 * 底色块在窄标签栏里会互相挤压。
 *
 * 宽度不够时横向滚动，不换行——换行会把正文往下推，滚动位置就跳了。
 */

export interface NoteTab {
  path: string
  title: string
}

export interface NoteTabsProps {
  active: string | null
  onClose: (path: string) => void
  onSelect: (path: string) => void
  tabs: NoteTab[]
}

export function NoteTabs({ active, onClose, onSelect, tabs }: NoteTabsProps) {
  const { t } = useI18n()
  if (tabs.length === 0) return null

  return (
    <div
      className="flex h-8 shrink-0 items-stretch gap-px overflow-x-auto border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      data-testid="note-tabs"
      role="tablist"
    >
      {tabs.map(tab => {
        const isActive = tab.path === active
        return (
          <div
            className={cn(
              'group relative flex min-w-0 shrink-0 items-center gap-1 pl-2.5 pr-1 text-[0.72rem] transition-colors',
              isActive ? 'text-foreground' : 'text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)'
            )}
            key={tab.path}
            role="tab"
          >
            <button
              aria-selected={isActive}
              className="max-w-40 truncate py-1.5 text-left"
              onClick={() => onSelect(tab.path)}
              title={tab.title}
              type="button"
            >
              {tab.title}
            </button>
            <button
              aria-label={t.common.close}
              className={cn(
                'flex size-4 shrink-0 items-center justify-center rounded-sm text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground',
                isActive ? 'opacity-70' : 'opacity-0 group-hover:opacity-70'
              )}
              onClick={() => onClose(tab.path)}
              type="button"
            >
              <Codicon name="close" size="0.625rem" />
            </button>
            {isActive && <span className="absolute inset-x-1 bottom-0 h-px bg-foreground" />}
          </div>
        )
      })}
    </div>
  )
}
