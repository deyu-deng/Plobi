import { Fragment, type ReactNode } from 'react'

import { TabDropdown } from '@/components/ui/tab-dropdown'
import type { IconComponent } from '@/lib/icons'
import { cn } from '@/lib/utils'

import { PAGE_INSET_X, PAGE_MAX_W } from '../layout-constants'

// The wide rail and the narrow dropdown swap at exactly the width where
// OverlaySplitLayout drops to a single column, so the rail never stacks.
const RAIL_HIDDEN = 'max-[47.5rem]:hidden'
const BAR_HIDDEN = 'hidden max-[47.5rem]:flex'

interface OverlaySplitLayoutProps {
  children: ReactNode
  className?: string
}

interface OverlaySidebarProps {
  children: ReactNode
  className?: string
}

interface OverlayMainProps {
  children: ReactNode
  className?: string
}

interface OverlayNavItemProps {
  active: boolean
  icon: IconComponent
  label: string
  // Renders as an indented child of another nav item: smaller icon and a
  // lighter active state so it never competes with the boxed parent item.
  nested?: boolean
  onClick: () => void
  trailing?: ReactNode
}

export function OverlaySplitLayout({ children, className }: OverlaySplitLayoutProps) {
  return (
    <div
      className={cn(
        // Narrow: one column, and pin rows to [nav-bar auto | main 1fr] — without
        // an explicit template the grid's default align-content:stretch splits the
        // height evenly across the two rows, shoving the content to mid-screen.
        'grid h-full min-h-0 flex-1 grid-cols-[13rem_minmax(0,1fr)] overflow-hidden bg-transparent max-[47.5rem]:grid-cols-1 max-[47.5rem]:grid-rows-[auto_minmax(0,1fr)]',
        className
      )}
    >
      {children}
    </div>
  )
}

export function OverlaySidebar({ children, className }: OverlaySidebarProps) {
  return (
    <aside
      className={cn(
        // pt clears the in-card close button (the OverlayView now insets the
        // whole card below the OS titlebar); the bg fills from the card's top
        // edge so there's no surface-colored gap above the sidebar.
        // 导航贴在"页"那一层上（不另涂一张白底）：Qoder 的左导航本来就不是卡，
        // 它和正文共用同一层浅底，白的是正文里那一张张分组卡。
        'flex min-h-0 flex-col gap-0.5 overflow-y-auto bg-transparent px-2.5 pb-3 pt-[calc(var(--titlebar-height)/2+1rem)]',
        className
      )}
    >
      {children}
    </aside>
  )
}

export function OverlayMain({ children, className }: OverlayMainProps) {
  return (
    <main
      className={cn(
        // Narrow: the OverlayNav dropdown bar already clears the titlebar, so
        // drop the tall top pad to a normal gap below it.
        // `.dl-page`：正文那一列是"页"，比外壳的画布亮一档——导航与详情这才分得开
        // （用户 11-03：「侧栏不应该是玻璃质感的吗」）。上下留 8px 让画布透出来，
        // 宽与内缩仍走 PAGE_MAX_W / PAGE_INSET_X，命令中心那一屏一个字不动。
        'dl-page mx-auto my-2 flex min-h-0 w-full flex-1 flex-col overflow-hidden pb-3 pt-[calc(var(--titlebar-height)/2+0.75rem)] max-[47.5rem]:pt-2',
        PAGE_MAX_W,
        PAGE_INSET_X,
        className
      )}
    >
      {children}
    </main>
  )
}

export function OverlayNavItem({ active, icon: Icon, label, nested, onClick, trailing }: OverlayNavItemProps) {
  return (
    <button
      className={cn(
        // 选中态不再画"描边盒子"（Qoder 的导航条目只变文字与一层软底）：描边会让
        // 每一条看起来像一张卡，和卡里的卡打架。半径走设计语言那一阶，不是随手 rounded-md。
        'flex h-7 w-full items-center justify-start gap-2 rounded-[var(--dl-radius-lg)] dl-squircle px-2 text-left text-[length:var(--conversation-text-font-size)] font-normal transition-colors',
        nested
          ? active
            ? 'bg-(--chrome-action-hover) font-medium text-foreground'
            : 'bg-transparent text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
          : active
            ? 'bg-(--ui-bg-tertiary) font-medium text-foreground'
            : 'bg-transparent text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      )}
      onClick={onClick}
      type="button"
    >
      <Icon
        className={cn(
          'shrink-0',
          nested ? 'size-3.5' : 'size-4',
          active ? 'text-foreground/80' : 'text-muted-foreground/80'
        )}
      />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing}
    </button>
  )
}

export interface OverlayNavLink {
  active: boolean
  icon: IconComponent
  id: string
  label: string
  onSelect: () => void
}

export interface OverlayNavGroup extends OverlayNavLink {
  /** Sub-links: expanded under the active group on the rail, always listed
   *  (flattened + indented) in the narrow dropdown. */
  children?: OverlayNavLink[]
  /** Visual break before this group — a spacer on the rail, a separator in
   *  the dropdown. */
  gapBefore?: boolean
}

// Data-driven pane nav: one model renders a persistent left rail on wide
// viewports and a single dropdown bar on narrow ones (matching the tab
// dropdown in PageSearchShell), so every OverlaySplitLayout pane degrades the
// same way instead of stacking its whole sidebar. Drop it in as the first
// child of an OverlaySplitLayout, before OverlayMain.
export function OverlayNav({ footer, groups }: { footer?: ReactNode; groups: OverlayNavGroup[] }) {
  return (
    <>
      <OverlaySidebar className={RAIL_HIDDEN}>
        {groups.map(group => (
          <Fragment key={group.id}>
            {group.gapBefore && <div aria-hidden className="h-2" />}
            <OverlayNavItem active={group.active} icon={group.icon} label={group.label} onClick={group.onSelect} />
            {group.children && group.active && (
              <div className="ml-3.5 flex flex-col gap-0.5 pl-1.5">
                {group.children.map(child => (
                  <OverlayNavItem
                    active={child.active}
                    icon={child.icon}
                    key={child.id}
                    label={child.label}
                    nested
                    onClick={child.onSelect}
                  />
                ))}
              </div>
            )}
          </Fragment>
        ))}
        {footer && <div className="mt-auto flex items-center gap-1 pt-2">{footer}</div>}
      </OverlaySidebar>

      {/* Narrow: ride the OverlayView titlebar strip so the dropdown shares the
          close button's row instead of taking its own. The bar is
          pointer-events-none (children opt back in) so the floating X underneath
          stays clickable; pr clears it, no-drag beats the strip's drag region,
          and the height matches the strip so the trigger lines up with the X. */}
      <div
        className={cn(
          'pointer-events-none relative z-20 h-[calc(var(--titlebar-height)+0.1875rem)] items-center justify-between gap-2 pl-3 pr-12',
          BAR_HIDDEN
        )}
      >
        <div className="pointer-events-auto min-w-0 [-webkit-app-region:no-drag]">
          <TabDropdown
            align="start"
            items={groups.flatMap(group => [
              {
                active: group.active && !group.children?.some(child => child.active),
                icon: group.icon,
                id: group.id,
                label: group.label,
                onSelect: group.onSelect,
                separatorBefore: group.gapBefore
              },
              ...(group.children ?? []).map(child => ({
                active: child.active,
                icon: child.icon,
                id: child.id,
                indent: true,
                label: child.label,
                onSelect: child.onSelect
              }))
            ])}
          />
        </div>
        {footer && (
          <div className="pointer-events-auto flex shrink-0 items-center gap-1 [-webkit-app-region:no-drag]">
            {footer}
          </div>
        )}
      </div>
    </>
  )
}
