import type { HTMLAttributes, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

export type RightRailTab =
  | 'agenda'
  | 'browser'
  | 'files'
  | 'progress'
  | 'review'
  | 'terminal'
export type RightRailTabId = RightRailTab | `file:${string}`

export interface RightRailTabDef {
  id: RightRailTabId
  icon: string
  title: string
  description?: string
}

export interface RightRailWorkbenchLabels {
  aria: string
  add: string
  close: (label: string) => string
  emptyTitle: string
  emptyBody: string
  noFolderTitle: string
  noFolderBody: string
}

function EmptyAction({
  description,
  icon,
  onClick,
  title
}: {
  description?: string
  icon: string
  onClick: () => void
  title: string
}) {
  return (
    <button
      className="group flex w-full items-center gap-3 rounded-xl border border-(--ui-stroke-tertiary) bg-muted/25 px-3 py-3 text-left transition-colors hover:bg-muted/60"
      onClick={onClick}
      type="button"
    >
      <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-muted/70 text-muted-foreground transition-colors group-hover:text-foreground">
        <Codicon name={icon} size="1.1rem" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[0.8rem] font-medium text-foreground">{title}</span>
        {description ? (
          <span className="mt-0.5 block truncate text-[0.66rem] text-muted-foreground">
            {description}
          </span>
        ) : null}
      </span>
    </button>
  )
}

export function RightRailWorkbench({
  activeTab,
  availableTabs,
  collapseIcon = 'layout-sidebar-right',
  collapseLabel,
  contents,
  dragHandleProps,
  labels,
  onCollapse,
  onTabChange,
  onTabClose,
  onTabOpen,
  tabs,
  trailing
}: {
  activeTab: RightRailTabId | null
  availableTabs: RightRailTabDef[]
  /** 收起这一栏的箭头方向：左右互换时朝另一边（用户 10-30：收起按钮跟卡片放一起）。 */
  collapseIcon?: string
  collapseLabel?: string
  contents: Partial<Record<string, ReactNode>>
  /** 卡片互换：抬头那一行当拖动把手（用户 10-31 拖右栏卡到对话卡上换位）。 */
  dragHandleProps?: HTMLAttributes<HTMLDivElement>
  labels: RightRailWorkbenchLabels
  onCollapse?: () => void
  onTabChange: (tab: RightRailTabId) => void
  onTabClose: (tab: RightRailTabId) => void
  onTabOpen: (tab: RightRailTabId) => void
  tabs: RightRailTabDef[]
  trailing?: ReactNode
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const hasTabs = tabs.length > 0
  const hasActions = availableTabs.length > 0

  useEffect(() => {
    if (!menuOpen) {
      return
    }

    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }

    window.addEventListener('mousedown', onPointerDown)

    return () => window.removeEventListener('mousedown', onPointerDown)
  }, [menuOpen])

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {hasTabs || onCollapse ? (
        <div
          className={cn(
            'flex h-8 shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-1',
            dragHandleProps && 'cursor-grab select-none active:cursor-grabbing'
          )}
          ref={menuRef}
          {...dragHandleProps}
        >
          {hasTabs ? (
          <div
            aria-label={labels.aria}
            className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            role="tablist"
          >
            {tabs.map(tab => {
              const active = tab.id === activeTab

              return (
                <div
                  className={cn(
                    'group/tab flex h-7 max-w-44 min-w-24 shrink-0 items-center rounded-md text-xs transition-colors',
                    active
                      ? 'bg-muted text-foreground'
                      : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
                  )}
                  data-active={active || undefined}
                  key={tab.id}
                >
                  <button
                    aria-label={labels.close(tab.title)}
                    className="ml-1.5 grid size-3.5 shrink-0 place-items-center rounded-sm text-muted-foreground outline-none transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:bg-muted"
                    onClick={() => onTabClose(tab.id)}
                    title={labels.close(tab.title)}
                    type="button"
                  >
                    <Codicon
                      className="col-start-1 row-start-1 opacity-100 group-hover/tab:opacity-0 group-focus-within/tab:opacity-0"
                      name={tab.icon}
                      size="0.875rem"
                    />
                    <Codicon
                      className="col-start-1 row-start-1 opacity-0 group-hover/tab:opacity-100 group-focus-within/tab:opacity-100"
                      name="close"
                      size="0.875rem"
                    />
                  </button>
                  <button
                    aria-selected={active}
                    className="flex h-full min-w-0 flex-1 items-center bg-transparent py-0 pr-2 pl-1.5 text-left text-inherit outline-none"
                    onClick={() => onTabChange(tab.id)}
                    role="tab"
                    title={tab.title}
                    type="button"
                  >
                    <span className="min-w-0 flex-1 truncate">{tab.title}</span>
                  </button>
                </div>
              )
            })}
          </div>
          ) : (
            <div className="min-w-0 flex-1" />
          )}

          {trailing}

          {hasActions && hasTabs ? (
            <div className="relative shrink-0">
              <button
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                aria-label={labels.add}
                className="grid size-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                onClick={() => setMenuOpen(open => !open)}
                title={labels.add}
                type="button"
              >
                <Codicon name="add" size="0.85rem" />
              </button>
              {menuOpen ? (
                <div
                  className="absolute top-8 right-0 z-20 w-48 rounded-lg border border-border bg-popover p-1 shadow-xl"
                  role="menu"
                >
                  {availableTabs.map(tab => (
                    <button
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[0.72rem] text-foreground hover:bg-accent"
                      key={tab.id}
                      onClick={() => {
                        setMenuOpen(false)
                        onTabOpen(tab.id)
                      }}
                      role="menuitem"
                      type="button"
                    >
                      <Codicon name={tab.icon} size="0.8rem" />
                      <span className="truncate">{tab.title}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {onCollapse ? (
            <button
              aria-label={collapseLabel}
              className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={onCollapse}
              title={collapseLabel}
              type="button"
            >
              <Codicon name={collapseIcon} size="0.85rem" />
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {hasTabs ? (
          tabs.map(tab => (
            <div
              aria-label={tab.title}
              className="flex min-h-0 flex-1 flex-col"
              data-testid={`right-rail-panel-${tab.id}`}
              hidden={tab.id !== activeTab}
              key={tab.id}
              role="tabpanel"
            >
              {contents[tab.id] ??
                (tab.id === 'files' ? (
                  <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
                    <Codicon name="folder" size="1.25rem" />
                    <p className="text-[0.78rem] font-medium text-foreground">{labels.noFolderTitle}</p>
                    <p className="text-[0.68rem] text-(--ui-text-tertiary)">{labels.noFolderBody}</p>
                  </div>
                ) : null)}
            </div>
          ))
        ) : hasActions ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-3 py-6">
            <div className="flex w-full max-w-[21rem] flex-col gap-2.5">
              {availableTabs.map(tab => (
                <EmptyAction
                  description={tab.description}
                  icon={tab.icon}
                  key={tab.id}
                  onClick={() => onTabOpen(tab.id)}
                  title={tab.title}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <Codicon name="layout-sidebar-right" size="1.25rem" />
            <p className="text-[0.78rem] font-medium text-foreground">{labels.emptyTitle}</p>
            <p className="text-[0.68rem] text-(--ui-text-tertiary)">{labels.emptyBody}</p>
          </div>
        )}
      </div>
    </div>
  )
}
