import { useStore } from '@nanostores/react'
import type { CSSProperties, ReactNode } from 'react'
import { useEffect, useSyncExternalStore } from 'react'

import { NotificationStack } from '@/components/notifications'
import { PaneShell } from '@/components/pane-shell'
import { FloatingPet } from '@/components/pet/floating-pet'
import { SidebarProvider } from '@/components/ui/sidebar'
import { useMediaQuery } from '@/hooks/use-media-query'
import { startScrollActivityWatch } from '@/lib/scroll-activity'
import { cn } from '@/lib/utils'
import {
  $cardFocusAnimating,
  $fileBrowserOpen,
  $panesFlipped,
  $shellCards,
  $sidebarAnimating,
  $sidebarOpen,
  FILE_BROWSER_DEFAULT_WIDTH,
  FILE_BROWSER_PANE_ID,
  setSidebarOpen
} from '@/store/layout'
import { $paneWidthOverride } from '@/store/panes'
import { $connection } from '@/store/session'
import { isSecondaryWindow } from '@/store/windows'

import { SIDEBAR_COLLAPSE_MEDIA_QUERY } from '../layout-constants'

import { useWindowControlsOverlayWidth } from './hooks/use-window-controls-overlay-width'
import { KeybindPanel } from './keybind-panel'
import { StatusbarControls, type StatusbarItem } from './statusbar-controls'
import { TITLEBAR_HEIGHT, titlebarControlsPosition } from './titlebar'
import { TitlebarControls, type TitlebarTool } from './titlebar-controls'

interface AppShellProps {
  children: ReactNode
  leftStatusbarItems?: readonly StatusbarItem[]
  leftTitlebarTools?: readonly TitlebarTool[]
  // Fixed-position overlays that must share <main>'s stacking context so pane
  // resize handles (z-20) paint above them. The persistent terminal lives here:
  // hoisting it to the root `overlays` layer (sibling of <main>, z above z-3)
  // would cover every pane's drag handle.
  mainOverlays?: ReactNode
  onOpenSettings: () => void
  overlays?: ReactNode
  /** L1: titlebar right-rail button toggles the agenda pane, not the file tree. */
  rightRail?: { open: boolean; toggle: () => void }
  statusbarItems?: readonly StatusbarItem[]
  titlebarTools?: readonly TitlebarTool[]
}

// Renderer-side fallback so layout snaps even when the main-process fullscreen event
// hasn't landed yet (e.g. dev reloads, before the IPC bridge is wired).
function subscribeWindowSize(cb: () => void) {
  window.addEventListener('resize', cb)
  window.addEventListener('fullscreenchange', cb)

  return () => {
    window.removeEventListener('resize', cb)
    window.removeEventListener('fullscreenchange', cb)
  }
}

const viewportIsFullscreen = () =>
  window.innerWidth >= window.screen.width && window.innerHeight >= window.screen.height

export function AppShell({
  children,
  leftStatusbarItems,
  leftTitlebarTools,
  mainOverlays,
  onOpenSettings,
  overlays,
  rightRail,
  statusbarItems,
  titlebarTools
}: AppShellProps) {
  const sidebarOpen = useStore($sidebarOpen)
  const sidebarAnimating = useStore($sidebarAnimating)
  // 「只显示这一张卡」那一下的列宽过渡（11-10）：只在焦点换掉的那 240ms 里给网格挂过渡，
  // 别的时候一点延迟都不留 —— 拖拽改宽与手动调列都不该被拖慢。
  // 曲线与左栏那把尺同一条（`styles.css:2076`），只是不写进样式表（那边在派别线手里）。
  const cardFocusAnimating = useStore($cardFocusAnimating)
  const fileBrowserOpen = useStore($fileBrowserOpen)
  const panesFlipped = useStore($panesFlipped)
  const shellCards = useStore($shellCards)
  const narrowViewport = useMediaQuery(SIDEBAR_COLLAPSE_MEDIA_QUERY)
  const fileBrowserWidthOverride = useStore($paneWidthOverride(FILE_BROWSER_PANE_ID))
  const connection = useStore($connection)
  const viewportFullscreen = useSyncExternalStore(subscribeWindowSize, viewportIsFullscreen, () => false)
  // 导条只在真正在滑的那一块上出现，停手就淡掉（用户 11-03）。一处全局接线：
  // 侧栏、正文、工作台与浮层里的每个 `overflow-y-auto` 都吃到，新加的也不会漏。
  useEffect(() => startScrollActivityWatch(), [])
  const isFullscreen = Boolean(connection?.isFullscreen) || viewportFullscreen
  // Every secondary window (new-session scratch, subagent watch, cmd-click
  // pop-out) is a compact side panel — none of them carry the full titlebar
  // tool cluster. Gate on isSecondaryWindow, never the narrower new-session flag.
  const hideTitlebarControls = isSecondaryWindow()
  const titlebarControls = titlebarControlsPosition(connection?.windowButtonPosition, isFullscreen)
  // Width Windows/WSLg reserve for the native min/max/close overlay (zero on
  // macOS, where window controls sit on the left and are reported via
  // windowButtonPosition instead). The right tool cluster has to clear them.
  // Prefer the EXACT width measured from the live Window Controls Overlay
  // (precise + self-correcting across DPI/host themes); fall back to the static
  // reservation the main process sends when the WCO API isn't available.
  const measuredOverlayWidth = useWindowControlsOverlayWidth()
  const staticOverlayWidth = connection?.nativeOverlayWidth ?? 0
  const nativeOverlayWidth = measuredOverlayWidth ?? staticOverlayWidth
  const titlebarToolsRight = nativeOverlayWidth > 0 ? `${nativeOverlayWidth}px` : '0.75rem'

  // When the native window controls overlay our titlebar band — Windows and
  // WSLg both paint Electron's Window Controls Overlay and report
  // nativeOverlayWidth > 0 — the right rail's editor-style tab strip (which
  // normally lives IN that band) would render at y=0 under the fixed titlebar
  // tool cluster and collide with it. Drop the right rail one titlebar-height so
  // it opens BELOW the band. macOS / plain Linux paint no overlay → 0 inset,
  // layout byte-for-byte unchanged. Consumed as --right-rail-top-inset.
  const rightRailTopInset = nativeOverlayWidth > 0 ? 'var(--titlebar-height)' : '0px'

  // The inset clears the top-left titlebar buttons when nothing covers the
  // window's left edge. Default layout: the sessions sidebar sits there.
  // Flipped layout: the file browser does instead. Both force-collapse to a
  // hover-reveal overlay (0px track) below the collapse breakpoint, so the edge
  // is uncovered there regardless of their stored open state. A standalone
  // session window renders no sidebar at all, so its edge is always uncovered.
  const collapsibleLeftPaneOpen = panesFlipped ? fileBrowserOpen : sidebarOpen

  const leftEdgePaneOpen =
    !isSecondaryWindow() && !narrowViewport && collapsibleLeftPaneOpen

  const titlebarContentInset = leftEdgePaneOpen
    ? 0
    : titlebarControls.left + TITLEBAR_HEIGHT + Math.round(TITLEBAR_HEIGHT / 2)

  // The static system cluster (haptics, profiles, settings, right-sidebar) is
  // hardcoded in TitlebarControls. Pane-supplied tools (preview's group) render
  // in a separate cluster anchored further left.
  //
  // Width math has to include the `gap-x-1` (0.25rem) between buttons:
  // N buttons + (N - 1) inner gaps, plus one extra 0.25rem of breathing room
  // between the pane-tool cluster and the system cluster so they don't sit
  // flush against each other. Modeled as N gaps (N - 1 inner + 1 trailing)
  // to keep the formula generic for any pane-tool count.
  // 卡片模式里顶栏右簇整个不在（设置/快捷键/触感反馈在左下图标条，右栏开关在对话卡抬头）；
  // 非卡片模式仍是四枚静态系统工具。
  const systemToolCount = shellCards ? 0 : 4
  const paneToolCount = titlebarTools?.filter(tool => !tool.hidden).length ?? 0
  const systemToolsWidth = `calc(${systemToolCount} * (var(--titlebar-control-size) + 0.25rem))`

  const fileBrowserWidth =
    fileBrowserWidthOverride !== undefined ? `${fileBrowserWidthOverride}px` : FILE_BROWSER_DEFAULT_WIDTH

  // Where the pane-tool cluster's right edge sits, measured from the inner
  // titlebar padding (--titlebar-tools-right). Two anchors:
  //   - file-browser closed → flush against static cluster's left edge
  //   - file-browser open   → flush against the file-browser pane's left edge
  //                           (= preview pane's right edge)
  const previewToolbarGap = fileBrowserOpen ? fileBrowserWidth : systemToolsWidth

  // Used by the drag region to know where the rightmost interactive element
  // ends. When pane tools are present, that's `gap + paneCount * controlSize
  // + paneCount * 0.25rem` (the leftmost button is at `tools-right + gap +
  // paneCount * (size + gap-x-1)`). Otherwise the static cluster's footprint
  // is enough.
  const titlebarToolsWidth =
    paneToolCount > 0
      ? `calc(${previewToolbarGap} + ${paneToolCount} * (var(--titlebar-control-size) + 0.25rem))`
      : systemToolsWidth

  return (
    <SidebarProvider
      className="h-screen min-h-0 flex-col bg-background"
      onOpenChange={setSidebarOpen}
      open={sidebarOpen}
      style={
        {
          // Alias for shadcn <Sidebar> descendants. Resolves to the chat-sidebar
          // pane track via PaneShell's emitted --pane-chat-sidebar-width.
          '--sidebar-width': 'var(--pane-chat-sidebar-width)',
          '--titlebar-height': `${TITLEBAR_HEIGHT}px`,
          '--titlebar-content-inset': `${titlebarContentInset}px`,
          '--titlebar-controls-left': `${titlebarControls.left}px`,
          '--titlebar-controls-top': `${titlebarControls.top}px`,
          '--titlebar-tools-right': titlebarToolsRight,
          '--titlebar-tools-width': titlebarToolsWidth,
          // Drops the right rail below the titlebar band when the OS/host paints
          // window controls over it (Windows/WSLg); 0px elsewhere.
          '--right-rail-top-inset': rightRailTopInset,
          // Anchor for the pane-tool cluster's right edge in TitlebarControls.
          // Sourced from the layout store rather than the PaneShell-emitted
          // --pane-*-width vars because the titlebar is a sibling of PaneShell
          // and CSS variables resolve at the consumer's scope.
          '--shell-preview-toolbar-gap': previewToolbarGap
        } as CSSProperties
      }
    >
      {!hideTitlebarControls && (
        <TitlebarControls
          leftTools={leftTitlebarTools}
          onOpenSettings={onOpenSettings}
          rightRail={rightRail}
          tools={titlebarTools}
        />
      )}

      {nativeOverlayWidth > 0 && (
        <div
          aria-hidden
          className="pointer-events-none fixed right-0 top-0 z-[4] h-(--titlebar-height) w-(--titlebar-tools-right) border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background)"
          data-native-overlay=""
        />
      )}

      <main
        className={cn(
          'relative z-3 flex min-h-0 w-full flex-1 flex-col overflow-hidden transition-none',
          shellCards && 'shell-cards',
          sidebarAnimating && 'shell-sidebar-animating'
        )}
      >
        <PaneShell
          className="min-h-0 flex-1"
          style={
            cardFocusAnimating
              ? { transition: 'grid-template-columns 200ms cubic-bezier(0.32, 0.72, 0, 1)' }
              : undefined
          }
        >
          <div
            aria-hidden="true"
            className="pointer-events-none absolute left-0 top-0 z-1 h-(--titlebar-height) w-(--titlebar-controls-left) [-webkit-app-region:drag]"
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute top-0 z-1 h-(--titlebar-height) left-[calc(var(--titlebar-controls-left)+(var(--titlebar-control-size)*2)+0.75rem)] right-[calc(var(--titlebar-tools-right)+var(--titlebar-tools-width)+0.75rem)] [-webkit-app-region:drag]"
          />

          {children}
        </PaneShell>

        {/* Fixed overlays scoped to main's stacking context (terminal). Rendered
            after PaneShell so it paints over pane content, but its z stays under
            the panes' z-20 resize handles, keeping every pane resizable. */}
        {mainOverlays}

        {/* The compact pop-out drops the statusbar — it's a scratch window, not
            the full shell. */}
        {!isSecondaryWindow() && !shellCards && (
          <StatusbarControls items={statusbarItems} leftItems={leftStatusbarItems} />
        )}
      </main>


      {overlays}

      {/* Keybind map dialog (titlebar ⌨ button / ⌘/). */}
      <KeybindPanel />

      {/* Mounted at the shell root (after overlays) so success/error toasts
          surface above every route and overlay — not just the chat view. */}
      <NotificationStack />

      {/* Petdex floating mascot — in-window, always-on-top, reactive to agent
          activity. Renders nothing unless a pet is installed + enabled. */}
      <FloatingPet />
    </SidebarProvider>
  )
}
