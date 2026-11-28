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
  $panesFlipped,
  $sidebarOpen,
  togglePanesFlipped,
  toggleSidebarOpen
} from '@/store/layout'

import { appViewForPath, isOverlayView } from '../routes'

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
}

export function TitlebarControls({ leftTools = [], tools = [] }: TitlebarControlsProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const sidebarOpen = useStore($sidebarOpen)
  const panesFlipped = useStore($panesFlipped)

  // 顶栏这一枚管的是**带图标的那条侧边栏**（会话栏）：换边换的是整条布局，「收起左栏」
  // 永远收它，并且跟着它挪到窗口控件那一侧、图标镜像。
  const sessionsEdge = { open: sidebarOpen, toggle: toggleSidebarOpen }

  // 卡片模式（10-31 用户）：换边换的是整条布局，「收起左栏」永远收那个带图标的侧边栏，
  // 并且跟着它挪到窗口控件那一侧、图标镜像；换边按钮留在左上角。
  const sidebarTool: TitlebarTool = {
    icon: <Codicon name={panesFlipped ? 'layout-sidebar-right' : 'layout-sidebar-left'} />,
    id: 'sidebar',
    label: sessionsEdge.open ? t.titlebar.hideSidebar : t.titlebar.showSidebar,
    onSelect: () => {
      triggerHaptic('tap')
      sessionsEdge.toggle()
    }
  }

  const sidebarOnRight = panesFlipped

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

  // 这三枚在左下图标条（用户 10-30），顶栏只剩左栏开关与换边。
  // 右栏开关整套住进对话卡抬头（10-31 用户：「点了之后图标又回到顶栏」），顶栏不再留重开。
  // ⚠️ **只有卡片这一副布局了**（用户 11-28 00:1x）⇒ 顶栏那两支（系统工具＋右栏开关）
  // **整条退役**。

  // While a full-screen overlay (settings, command center, …) is open it should
  // visually own the window. These control clusters are `fixed` at a higher
  // z-index than the overlay card, so they'd otherwise bleed over it — hide them
  // and let the overlay's own chrome (close button, drag region) take over.
  if (isOverlayView(appViewForPath(location.pathname))) {
    return null
  }

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

      {sidebarOnRight && (
        <div
          aria-label={t.shell.appControls}
          className="fixed right-(--titlebar-tools-right) top-(--titlebar-controls-top) z-70 flex flex-row items-center justify-end gap-x-1 pointer-events-auto select-none [-webkit-app-region:no-drag]"
        >
          <TitlebarToolButton navigate={navigate} tool={sidebarTool} />
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
