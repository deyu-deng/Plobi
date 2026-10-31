import { type ComponentProps, type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Tip, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { dockFitCount, dockItemWidth } from './dock-layout'

// Shared chrome styling for interactive statusbar items (button / link / menu
// trigger). The 'text' variant intentionally omits hover/transition/disabled.
const STATUSBAR_ACTION_CLASS =
  'inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground disabled:cursor-default disabled:opacity-45'

export interface StatusbarMenuItem {
  id: string
  icon?: ReactNode
  label: string
  className?: string
  disabled?: boolean
  hidden?: boolean
  href?: string
  onSelect?: () => void
  title?: string
  to?: string
}

export interface StatusbarItem {
  id: string
  label?: ReactNode
  detail?: ReactNode
  icon?: ReactNode
  className?: string
  disabled?: boolean
  hidden?: boolean
  href?: string
  menuAlign?: 'center' | 'end' | 'start'
  menuClassName?: string
  // A render fn receives a `close()` to dismiss the popover from inside the content.
  menuContent?: ((close: () => void) => ReactNode) | ReactNode
  menuItems?: readonly StatusbarMenuItem[]
  onSelect?: (modifiers: StatusbarSelectModifiers) => void
  title?: string
  to?: string
  variant?: 'action' | 'link' | 'menu' | 'text'
}

export interface StatusbarSelectModifiers {
  shiftKey: boolean
}

export type StatusbarItemSide = 'left' | 'right'
export type SetStatusbarItemGroup = (id: string, items: readonly StatusbarItem[], side?: StatusbarItemSide) => void

interface StatusbarControlsProps extends ComponentProps<'footer'> {
  /**
   * 卡片模式的左下图标条（用户 10-31）：只留图标、整排跟着左栏宽度走。
   * `dockTools` 是设置/快捷键/触感反馈那三枚——它们只在这条里出现，顶栏不再画。
   */
  dock?: boolean
  /** `rail` = 宽度跟左栏走；`edge` = 左栏收起/换边时按内容宽。 */
  dockAnchor?: 'rail' | 'edge'
  dockTools?: readonly StatusbarItem[]
  leftItems?: readonly StatusbarItem[]
  items?: readonly StatusbarItem[]
}

export function StatusbarControls({
  className,
  dock = false,
  dockAnchor = 'edge',
  dockTools = [],
  leftItems = [],
  items = [],
  ...props
}: StatusbarControlsProps) {
  const navigate = useNavigate()
  const allItems = useMemo(() => [...dockTools, ...leftItems, ...items], [dockTools, items, leftItems])

  if (dock) {
    return <StatusbarDock anchor={dockAnchor} className={className} items={allItems} {...props} />
  }

  return (
    <footer
      className={cn(
        'flex h-5 shrink-0 items-stretch justify-between gap-2 border-t border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) px-1 py-0 text-(--ui-text-tertiary) [-webkit-app-region:no-drag]',
        className
      )}
      data-slot="statusbar"
      {...props}
    >
      {/* `overflow-x-clip` (not `overflow-x-auto`) so a wide status item — for
          example "Connecting…" on a fresh/untitled session — can't paint a
          horizontal scrollbar across the bottom of the window. Items already
          `truncate` their labels, so clipping is the right behavior. */}
      <div className="flex min-w-0 items-stretch gap-0.5 overflow-x-clip">
        {leftItems
          .filter(item => !item.hidden)
          .map(item => (
            <StatusbarItemView item={item} key={`left:${item.id}`} navigate={navigate} />
          ))}
      </div>
      <div className="flex min-w-0 items-stretch gap-0.5 overflow-x-clip">
        {items
          .filter(item => !item.hidden)
          .map(item => (
            <StatusbarItemView item={item} key={`right:${item.id}`} navigate={navigate} />
          ))}
      </div>
    </footer>
  )
}

/**
 * 卡片模式的左下图标条：一格一个图标，排到左栏右沿为止；放不下的收进「⋯」。
 * 宽度是量出来的（ResizeObserver），不是写死的格数——左栏可拖宽（186–300）。
 */
function StatusbarDock({
  anchor,
  className,
  items,
  ...props
}: { anchor: 'rail' | 'edge'; items: readonly StatusbarItem[] } & ComponentProps<'footer'>) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const dockRef = useRef<HTMLElement | null>(null)
  const visible = useMemo(() => items.filter(item => !item.hidden), [items])
  const [fitCount, setFitCount] = useState(visible.length)

  useEffect(() => {
    const element = dockRef.current

    if (!element) {
      return
    }

    const measure = () => {
      const available = element.clientWidth - 4
      setFitCount(dockFitCount(visible.map(dockItemWidth), available))
    }

    measure()

    const observer = new ResizeObserver(measure)
    observer.observe(element)

    return () => observer.disconnect()
  }, [visible])

  const shown = visible.slice(0, Math.min(fitCount, visible.length))
  const overflow = visible.slice(shown.length)

  return (
    <footer
      className={cn('[-webkit-app-region:no-drag]', className)}
      data-dock=""
      data-dock-anchor={anchor}
      data-slot="statusbar"
      ref={dockRef}
      {...props}
    >
      {shown.map(item => (
        <StatusbarItemView dock item={item} key={`dock:${item.id}`} navigate={navigate} />
      ))}
      {overflow.length > 0 ? <DockOverflow items={overflow} label={t.shell.statusbar.more} /> : null}
    </footer>
  )
}

/** 放不下的那几格：先给清单（图标 + 话），带面板的那几格点开就直接是那个面板。 */
function DockOverflow({ items, label }: { items: readonly StatusbarItem[]; label: string }) {
  const navigate = useNavigate()
  const [panelId, setPanelId] = useState<string | null>(null)
  const panel = items.find(item => item.id === panelId)

  return (
    <DropdownMenu
      onOpenChange={open => {
        if (!open) {
          setPanelId(null)
        }
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          aria-label={label}
          data-dock-item=""
          data-variant="action"
          title={label}
          type="button"
        >
          <Codicon name="ellipsis" size="0.875rem" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className={cn('w-56', panel && 'p-0')}
        side="top"
        sideOffset={6}
      >
        {panel ? (
          typeof panel.menuContent === 'function' ? panel.menuContent(() => setPanelId(null)) : panel.menuContent
        ) : (
          items.map(item => (
            <DropdownMenuItem
              className="gap-2 text-foreground focus:bg-accent [&_svg]:size-4"
              key={item.id}
              onSelect={event => {
                if (item.menuContent) {
                  // 面板留在同一张下拉里，别把它关掉
                  event.preventDefault()
                  setPanelId(item.id)

                  return
                }

                if (item.to) {
                  navigate(item.to)
                }

                if (item.href) {
                  window.open(item.href, '_blank', 'noreferrer')
                }

                item.onSelect?.({ shiftKey: false })
              }}
            >
              {item.icon}
              <span className="truncate">
                {typeof item.label === 'string' ? item.label : (item.title ?? item.id)}
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function StatusbarItemView({
  dock = false,
  item,
  navigate
}: {
  dock?: boolean
  item: StatusbarItem
  navigate: ReturnType<typeof useNavigate>
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  // 图标条里没有文字可读，tooltip 退到 label 那一句；a11y 也用它当名字。
  const tip = item.title ?? (typeof item.label === 'string' ? item.label : undefined)
  const dockLabel = dock ? (tip ?? item.id) : undefined
  const dockAttrs = {
    'data-dock-item': dock ? '' : undefined,
    'data-variant': dock ? (item.variant ?? 'action') : undefined
  }

  const content = (
    <>
      {item.icon}
      {(item.label && (!dock || item.variant === 'text')) ? <span className="truncate">{item.label}</span> : null}
      {(item.detail && (!dock || item.variant === 'text')) ? (
        <span className="truncate text-muted-foreground/80">{item.detail}</span>
      ) : null}
    </>
  )

  if (item.variant === 'menu' && (item.menuContent || (item.menuItems && item.menuItems.length > 0))) {
    // The `Tip` helper can't wrap a menu: its TooltipTrigger needs a DOM child,
    // but DropdownMenu's Root renders no element, so the hover listeners never
    // land on the button and the tooltip silently never shows. Compose the two
    // trigger Slots directly onto the same <button> instead (both asChild), the
    // way profile-switcher.tsx stacks Popover/ContextMenu/Tooltip triggers.
    const trigger = (
      <DropdownMenuTrigger asChild>
        <button
          aria-label={dockLabel}
          className={cn(STATUSBAR_ACTION_CLASS, item.className)}
          disabled={item.disabled}
          title={dock ? tip : undefined}
          type="button"
          {...dockAttrs}
        >
          {content}
        </button>
      </DropdownMenuTrigger>
    )

    return (
      <DropdownMenu onOpenChange={setMenuOpen} open={menuOpen}>
        {item.title ? (
          <TooltipProvider delayDuration={0}>
            <Tooltip>
              <TooltipTrigger asChild>{trigger}</TooltipTrigger>
              <TooltipContent>{item.title}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : (
          trigger
        )}
        <DropdownMenuContent
          align={item.menuAlign ?? 'start'}
          className={cn('w-56', item.menuContent && 'p-0', item.menuClassName)}
          side="top"
          sideOffset={8}
        >
          {item.menuContent
            ? typeof item.menuContent === 'function'
              ? item.menuContent(() => setMenuOpen(false))
              : item.menuContent
            : (item.menuItems ?? [])
                .filter(menuItem => !menuItem.hidden)
                .map(menuItem => (
                  <DropdownMenuItem
                    className={cn('gap-2 text-foreground focus:bg-accent [&_svg]:size-4', menuItem.className)}
                    disabled={menuItem.disabled}
                    key={menuItem.id}
                    onSelect={() => {
                      if (menuItem.to) {
                        navigate(menuItem.to)
                      }

                      menuItem.onSelect?.()
                    }}
                  >
                    {menuItem.href ? (
                      <a
                        className="inline-flex w-full items-center gap-2"
                        href={menuItem.href}
                        rel="noreferrer"
                        target="_blank"
                      >
                        {menuItem.icon}
                        <span className="truncate">{menuItem.label}</span>
                      </a>
                    ) : (
                      <>
                        {menuItem.icon}
                        <span className="truncate">{menuItem.label}</span>
                      </>
                    )}
                  </DropdownMenuItem>
                ))}
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

  if (item.variant === 'text' && !item.onSelect && !item.to && !item.href) {
    return (
      <Tip label={item.title}>
        <div
          className={cn(
            'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
            item.className
          )}
          {...dockAttrs}
        >
          {content}
        </div>
      </Tip>
    )
  }

  if (item.href || item.variant === 'link') {
    return (
      <Tip label={tip}>
        <a
          aria-label={dockLabel}
          className={cn(STATUSBAR_ACTION_CLASS, item.className)}
          href={item.href}
          rel="noreferrer"
          target="_blank"
          title={dock ? tip : undefined}
          {...dockAttrs}
        >
          {content}
        </a>
      </Tip>
    )
  }

  return (
    <Tip label={tip}>
      <button
        aria-label={dockLabel}
        className={cn(STATUSBAR_ACTION_CLASS, item.className)}
        disabled={item.disabled}
        onClick={event => {
          if (item.to) {
            navigate(item.to)
          }

          item.onSelect?.({ shiftKey: event.shiftKey })
        }}
        title={dock ? tip : undefined}
        type="button"
        {...dockAttrs}
      >
        {content}
      </button>
    </Tip>
  )
}
