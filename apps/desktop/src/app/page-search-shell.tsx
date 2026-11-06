import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { ResponsiveTabs } from '@/components/ui/tab-dropdown'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { HOME_ROUTE } from './routes'

// Tabs are data, not nodes: the shell owns their presentation so every page
// gets the same behavior — a centered TextTab row on wide viewports that
// collapses into a dropdown when the header can't fit both search and tabs.
export interface PageShellTab {
  id: string
  label: string
  /** Count badge. `null` = still loading (renders a skeleton); `undefined` = no badge. */
  meta?: string | number | null
}

interface PageSearchShellProps extends React.ComponentProps<'section'> {
  children: ReactNode
  tabs?: PageShellTab[]
  activeTab?: string
  onTabChange?: (id: string) => void
  /** Secondary filters shown full-width on their own row below (expands). */
  filters?: ReactNode
  onSearchChange: (value: string) => void
  searchPlaceholder: string
  /** Data-derived rotating placeholder nudges (see SearchField.hints). */
  searchHints?: string[]
  searchValue: string
  /** Hide the search field when there's nothing to search (empty dataset). */
  searchHidden?: boolean
  /** Right-aligned control in the header's trailing cell (e.g. a refresh button)
   *  so mouse users get a visible affordance for the refresh hotkey. */
  searchTrailingAction?: ReactNode
  /** 这一页是不是顶层那一屏。嵌进设置里的那一份要传 false：头顶那枚返回键会
   *  把人直接送回对话，把宿主那一屏整张撤掉（用户 11-05 的截图里就是这样）。 */
  showBack?: boolean
  /** 表头：这一屏属于哪个 Agent（主界面侧边栏点进来的能力页）。只写 Agent 名，不加修饰。 */
  pageTitle?: string
}

function ShellTabs({
  tabs,
  activeTab,
  onTabChange
}: {
  tabs: PageShellTab[]
  activeTab?: string
  onTabChange?: (id: string) => void
}) {
  return (
    <ResponsiveTabs
      onChange={id => onTabChange?.(id)}
      tabs={tabs}
      value={activeTab ?? tabs[0]?.id ?? ''}
      wideClassName="justify-center"
    />
  )
}

export function PageSearchShell({
  children,
  className,
  tabs,
  activeTab,
  onTabChange,
  filters,
  onSearchChange,
  searchPlaceholder,
  searchHints,
  searchValue,
  searchHidden = false,
  searchTrailingAction,
  showBack = true,
  pageTitle,
  ...props
}: PageSearchShellProps) {
  const hasTabs = (tabs?.length ?? 0) > 0
  const navigate = useNavigate()
  const { t } = useI18n()

  return (
    <section
      {...props}
      className={cn('flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)', className)}
    >
      {/*
        Header lives in the page body, below the window chrome (the shell floats
        traffic lights over the top titlebar-height strip, which the `pt` clears
        and leaves draggable). Search left, tabs centered on the page via the
        1fr/auto/1fr grid; the trailing 1fr keeps the center honest.
      */}
      {/*
        IMPORTANT: do NOT put `-webkit-app-region: drag` on this header. It spans
        full width over the band where the floating titlebar icon clusters live,
        and an overlapping OS drag region eats their clicks at the compositor
        level (pointer-events / no-drag carve-outs across separate stacking
        contexts don't reliably fix it on macOS). The shell already supplies a
        draggable titlebar strip that is `calc()`'d around the icon clusters
        (see app-shell.tsx), so window dragging still works here.
      */}
      <div className="shrink-0">
        {/* 这一行一直在：即便这一页既没搜索也没分页，也要有一条回对话的路（用户 11-03：
            「打开左侧边栏上部分的组件，有些没有返回操作，退回原聊天界面太麻烦了」）。
            返回键在最左、与搜索同一行——Qoder 那几个页面也是这个位置。 */}
        <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 px-3 pb-2 pt-[calc(var(--titlebar-height)+0.5rem)]">
          <div className="flex min-w-0 items-center justify-start gap-1">
            {showBack ? (
              <Button
                aria-label={t.common.back}
                className="shrink-0 text-(--ui-text-tertiary)"
                onClick={() => navigate(HOME_ROUTE)}
                size="icon-xs"
                title={t.common.back}
                variant="ghost"
              >
                <Codicon name="arrow-left" />
              </Button>
            ) : null}
            {!searchHidden && (
              <SearchField
                containerClassName="max-w-[45vw]"
                hints={searchHints}
                onChange={onSearchChange}
                placeholder={searchPlaceholder}
                value={searchValue}
              />
            )}
          </div>
          {hasTabs ? <ShellTabs activeTab={activeTab} onTabChange={onTabChange} tabs={tabs!} /> : <span />}
          <div className="flex min-w-0 items-center justify-end">{searchTrailingAction}</div>
        </div>
        {pageTitle ? (
          // 表头：一行 Agent 名。主界面侧边栏点进来的能力页才有；设置里那份全局的
          // 不传（用户 11-05：全局页不加任何标识）。
          <div className="flex min-w-0 items-center px-3 pb-2">
            <h2 className="truncate text-[0.92rem] font-medium text-(--ui-text-primary)">{pageTitle}</h2>
          </div>
        ) : null}
        {filters ? <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 pb-2">{filters}</div> : null}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden bg-(--ui-chat-surface-background)">{children}</div>
    </section>
  )
}
