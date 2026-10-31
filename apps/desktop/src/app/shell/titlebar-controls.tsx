import { useStore } from '@nanostores/react'
import type { ComponentProps, ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { cn } from '@/lib/utils'
import {
  $fileBrowserOpen,
  $panesFlipped,
  $shellCards,
  $sidebarOpen,
  toggleFileBrowserOpen,
  togglePanesFlipped,
  toggleSidebarOpen
} from '@/store/layout'

import { appViewForPath, isOverlayView } from '../routes'

import { useSystemTools } from './system-tools'
import { titlebarButtonClass } from './titlebar'

export interface TitlebarTool {
  id: string
  label: string
  active?: boolean
  className?: string
  disabled?: boolean
  hidden?: boolean
  href?: string
  icon: ReactNode
  onSelect?: () => void
  title?: string
  to?: string
}

export type TitlebarToolSide = 'left' | 'right'
export type SetTitlebarToolGroup = (id: string, tools: readonly TitlebarTool[], side?: TitlebarToolSide) => void

interface TitlebarControlsProps extends ComponentProps<'div'> {
  leftTools?: readonly TitlebarTool[]
  tools?: readonly TitlebarTool[]
  onOpenSettings: () => void
  /**
   * When set (L1 secretary shell), the right-rail titlebar button toggles this
   * pane instead of the file browser — on L1 the file tree is disabled and the
   * agenda timeline owns the right column.
   */
  rightRail?: { open: boolean; toggle: () => void }
}

export function TitlebarControls({
  leftTools = [],
  tools = [],
  onOpenSettings,
  rightRail
}: TitlebarControlsProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const systemTools = useSystemTools(onOpenSettings)
  const fileBrowserOpen = useStore($fileBrowserOpen)
  const sidebarOpen = useStore($sidebarOpen)
  const panesFlipped = useStore($panesFlipped)
  const shellCards = useStore($shellCards)

  // Each titlebar button controls the pane physically on its side, so a flip
  // swaps which pane each one toggles. Default: sessions left, file browser
  // right. Flipped: file browser left, sessions right. On L1 the right column
  // is the agenda timeline (file browser is disabled), so `rightRail` steers
  // that button at the agenda pane instead.
  const fileBrowserEdge = { open: fileBrowserOpen, toggle: toggleFileBrowserOpen }
  const sessionsEdge = { open: sidebarOpen, toggle: toggleSidebarOpen }
  const agendaEdge = rightRail ?? fileBrowserEdge
  const leftEdge = panesFlipped ? agendaEdge : sessionsEdge
  const rightEdge = panesFlipped ? sessionsEdge : agendaEdge

  // 卡片模式（10-31 用户）：换边换的是整条布局，「收起左栏」永远收那个带图标的侧边栏，
  // 并且跟着它挪到窗口控件那一侧、图标镜像；换边按钮留在左上角。
  const sidebarTool: TitlebarTool = {
    icon: <Codicon name={shellCards && panesFlipped ? 'layout-sidebar-right' : 'layout-sidebar-left'} />,
    id: 'sidebar',
    label: (shellCards ? sessionsEdge : leftEdge).open ? t.titlebar.hideSidebar : t.titlebar.showSidebar,
    onSelect: () => {
      triggerHaptic('tap')
      ;(shellCards ? sessionsEdge : leftEdge).toggle()
    }
  }
  const sidebarOnRight = shellCards && panesFlipped

  const leftToolbarTools: TitlebarTool[] = [
    ...(sidebarOnRight ? [] : [sidebarTool]),
    {
      icon: <Codicon name="arrow-swap" />,
      id: 'flip-panes',
      label: t.titlebar.swapSidebarSides,
      onSelect: () => {
        triggerHaptic('tap')
        togglePanesFlipped()
      },
      title: t.titlebar.swapSidebarSidesTitle
    },
    ...leftTools
  ]

  const rightSidebarTool: TitlebarTool = {
    icon: <Codicon name="layout-sidebar-right" />,
    id: 'right-sidebar',
    label: rightEdge.open ? t.titlebar.hideRightSidebar : t.titlebar.showRightSidebar,
    onSelect: () => {
      triggerHaptic('tap')
      rightEdge.toggle()
    }
  }

  // 卡片模式里这三枚在左下图标条（用户 10-30），顶栏只剩左栏开关与换边。
  // 右栏开关整套住进对话卡抬头（10-31 用户：「点了之后图标又回到顶栏」），顶栏不再留重开。
  const showSystemTools = !shellCards
  const showRightRailTool = !shellCards

  // While a full-screen overlay (settings, command center, …) is open it should
  // visually own the window. These control clusters are `fixed` at a higher
  // z-index than the overlay card, so they'd otherwise bleed over it — hide them
  // and let the overlay's own chrome (close button, drag region) take over.
  if (isOverlayView(appViewForPath(location.pathname))) {
    return null
  }

  const visibleSystemTools = systemTools.filter(tool => !tool.hidden)
  const settingsTool = visibleSystemTools.find(tool => tool.id === 'settings')
  const visibleSystemToolsBeforeSettings = visibleSystemTools.filter(tool => tool.id !== 'settings')
  const visiblePaneTools = tools.filter(tool => !tool.hidden)

  return (
    <>
      <div
        aria-label={t.shell.windowControls}
        className="fixed left-(--titlebar-controls-left) top-(--titlebar-controls-top) z-70 flex translate-y-0.5 flex-row items-center gap-x-1 pointer-events-auto select-none [-webkit-app-region:no-drag]"
      >
        {leftToolbarTools
          .filter(tool => !tool.hidden)
          .map(tool => (
            <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
          ))}
      </div>

      {/*
        Pane-scoped tools (preview's monitor / devtools / refresh / X) render
        as their own fixed cluster. AppShell sets --shell-preview-toolbar-gap
        to either the static cluster's width (file-browser closed → cluster
        sits flush against system tools) or the file-browser pane's width
        (file-browser open → cluster sits flush against the file-browser pane,
        i.e. at the preview pane's right edge). No margin hacks needed.
      */}
      {visiblePaneTools.length > 0 && (
        <div
          aria-label={t.shell.paneControls}
          className="fixed top-[calc(var(--titlebar-controls-top)+var(--right-rail-top-inset,0px))] right-[calc(var(--titlebar-tools-right)+var(--shell-preview-toolbar-gap,0))] z-70 flex flex-row items-center gap-x-1 pointer-events-auto select-none [-webkit-app-region:no-drag]"
        >
          {visiblePaneTools.map(tool => (
            <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
          ))}
        </div>
      )}

      {(showSystemTools || showRightRailTool || sidebarOnRight) && (
        <div
          aria-label={t.shell.appControls}
          className="fixed right-(--titlebar-tools-right) top-(--titlebar-controls-top) z-70 flex flex-row items-center justify-end gap-x-1 pointer-events-auto select-none [-webkit-app-region:no-drag]"
        >
          {showSystemTools &&
            visibleSystemToolsBeforeSettings.map(tool => (
              <TitlebarToolButton key={tool.id} navigate={navigate} tool={tool} />
            ))}
          {showSystemTools && settingsTool && <TitlebarToolButton navigate={navigate} tool={settingsTool} />}
          {showRightRailTool && <TitlebarToolButton navigate={navigate} tool={rightSidebarTool} />}
          {sidebarOnRight && <TitlebarToolButton navigate={navigate} tool={sidebarTool} />}
        </div>
      )}
    </>
  )
}

function TitlebarToolButton({ navigate, tool }: { navigate: ReturnType<typeof useNavigate>; tool: TitlebarTool }) {
  // Titlebar actions never show an active background — state reads from the
  // icon itself (e.g. the mute/unmute glyph). aria-pressed still carries it
  // for a11y.
  const className = cn(titlebarButtonClass, 'bg-transparent select-none', tool.className)

  if (tool.href) {
    return (
      <Tip label={tool.title ?? tool.label}>
        <Button asChild className={className} size="icon-titlebar" variant="ghost">
          <a
            aria-label={tool.label}
            href={tool.href}
            onPointerDown={event => event.stopPropagation()}
            rel="noreferrer"
            target="_blank"
          >
            {tool.icon}
          </a>
        </Button>
      </Tip>
    )
  }

  return (
    <Tip label={tool.title ?? tool.label}>
      <Button
        aria-label={tool.label}
        aria-pressed={tool.active ?? undefined}
        className={className}
        disabled={tool.disabled}
        onClick={() => {
          if (tool.to) {
            navigate(tool.to)
          }

          tool.onSelect?.()
        }}
        onPointerDown={event => event.stopPropagation()}
        size="icon-titlebar"
        type="button"
        variant="ghost"
      >
        {tool.icon}
      </Button>
    </Tip>
  )
}
