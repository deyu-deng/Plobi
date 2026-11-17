import { type ReactNode, type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { Button } from '@/components/ui/button'
import { ChevronDown } from '@/lib/icons'
import { cn } from '@/lib/utils'

/**
 * 选区浮窗（用户 11-17「选中一段就有那个小浮窗」）。
 *
 * 一份组件两处用：聊天里选中一段 ⇒ 复制／引用／发给别的智能体；文件里选中一段 ⇒
 * 复制／加入本会话／让智能体改这一段。**动作由调用方给**，本档只管「贴着选区浮起来」
 * 与「点它不许把选区点塌」这两件事。
 *
 * 定位 maths 是从那份**没人挂它**的死代码（`app/chat/right-rail/selection-to-chat.tsx`，
 * 预览轨删掉时它留在原地）搬过来的，写成了纯函数——jsdom 不给真实 Range 报布局，
 * 能测的就是这段 maths（死代码当年也是这么测的）。
 */

/** 浮窗与选区之间留多远。 */
export const TOOLBAR_GAP = 6
/** 浮窗离容器边至少留这么多（贴边会被切掉）。 */
export const TOOLBAR_EDGE = 8

export interface SelectionRect {
  bottom: number
  left: number
  right: number
  top: number
}

export interface ContainerRect {
  bottom: number
  height: number
  left: number
  top: number
  width: number
}

export interface ToolbarSize {
  height: number
  width: number
}

/**
 * 浮窗落点：贴着选区下方；下方放不下就翻到选区上方；左右都夹在容器里。
 *
 * 纯函数——见档首那段话。
 */
export function anchorForSelection(
  container: ContainerRect,
  rect: SelectionRect,
  size: ToolbarSize
): { left: number; top: number } {
  const minLeft = container.left + TOOLBAR_EDGE
  const maxLeft = Math.max(minLeft, container.left + container.width - size.width - TOOLBAR_EDGE)
  const minTop = container.top + TOOLBAR_EDGE
  const maxTop = Math.max(minTop, container.top + container.height - size.height - TOOLBAR_EDGE)

  const below = rect.bottom + TOOLBAR_GAP
  const above = rect.top - TOOLBAR_GAP - size.height

  let top = below

  if (below > maxTop && above >= minTop) {
    top = above
  }

  top = Math.min(Math.max(top, minTop), maxTop)

  const left = Math.min(Math.max(rect.left, minLeft), maxLeft)

  return { left, top }
}

/** 动作拿到的东西：选中的字 ＋ 选区本身（文件侧要靠它算行号）。 */
export interface SelectionPayload {
  range: Range
  text: string
}

export interface SelectionToolbarMenuItem {
  id: string
  label: string
  onSelect: (payload: SelectionPayload) => void
}

export interface SelectionToolbarAction {
  id: string
  label: string
  icon?: ReactNode
  onSelect?: (payload: SelectionPayload) => void
  /** 带下拉的动作：点它开一层菜单，菜单里才是具体选项（发给哪个智能体）。 */
  menu?: SelectionToolbarMenuItem[]
}

/** 选区里的字。走 `cloneContents` 而不是 `Range.toString()`——后者在 jsdom 上不认。 */
export function textOfRange(range: Range): string {
  const holder = document.createElement('div')

  holder.append(range.cloneContents())

  return holder.textContent ?? ''
}

/**
 * 选区在视口里的那一只框。
 *
 * jsdom 的 `Range` 没有 `getBoundingClientRect`（真浏览器有）——缺了就给一只空框，
 * 落点 maths 照样算得出来（只是没有真实坐标而已）。
 */
export function rangeRect(range: Range): SelectionRect {
  const rect = (range as Range & { getBoundingClientRect?: () => DOMRect }).getBoundingClientRect?.()

  if (!rect) {
    return { bottom: 0, left: 0, right: 0, top: 0 }
  }

  return { bottom: rect.bottom, left: rect.left, right: rect.right, top: rect.top }
}

/**
 * 选区**之前**在这个容器里的那部分字。数它有几行，就知道选区从第几行开始。
 */
export function textBeforeRange(container: Node, range: Range): string {
  const slice = range.cloneRange()

  slice.selectNodeContents(container)
  slice.setEnd(range.startContainer, range.startOffset)

  const holder = document.createElement('div')

  holder.append(slice.cloneContents())

  return holder.textContent ?? ''
}

export interface SelectionToolbarProps {
  actions: SelectionToolbarAction[]
  /** 选区必须在这个容器里，浮窗才出现（别把别的面板也认领了）。 */
  containerRef: RefObject<HTMLElement | null>
  testId?: string
}

export function SelectionToolbar({ actions, containerRef, testId = 'selection-toolbar' }: SelectionToolbarProps) {
  const [text, setText] = useState<null | string>(null)
  const [anchor, setAnchor] = useState<null | { left: number; top: number }>(null)
  const [openMenu, setOpenMenu] = useState<null | string>(null)
  const rangeRef = useRef<null | Range>(null)
  const toolbarRef = useRef<HTMLDivElement | null>(null)

  const hide = useCallback(() => {
    rangeRef.current = null
    setText(null)
    setAnchor(null)
    setOpenMenu(null)
  }, [])

  const reposition = useCallback(() => {
    const host = containerRef.current
    const bar = toolbarRef.current
    const range = rangeRef.current

    if (!host || !bar || !range) {
      setAnchor(null)

      return
    }

    const rect = rangeRect(range)

    setAnchor(
      anchorForSelection(host.getBoundingClientRect(), rect, {
        height: bar.offsetHeight,
        width: bar.offsetWidth
      })
    )
  }, [containerRef])

  const sync = useCallback(() => {
    const host = containerRef.current
    const selection = window.getSelection()

    if (!host || !selection || !selection.rangeCount) {
      hide()

      return
    }

    const range = selection.getRangeAt(0)

    if (range.collapsed || !host.contains(range.commonAncestorContainer)) {
      hide()

      return
    }

    const selected = textOfRange(range).trim()

    if (!selected) {
      hide()

      return
    }

    rangeRef.current = range
    setText(selected)
  }, [containerRef, hide])

  useEffect(() => {
    document.addEventListener('mouseup', sync)
    document.addEventListener('keyup', sync)

    return () => {
      document.removeEventListener('mouseup', sync)
      document.removeEventListener('keyup', sync)
    }
  }, [sync])

  // 浮窗是固定定位的：容器滚一下，它得跟着选区走，不然就停在原地。
  useEffect(() => {
    if (!text) {
      return
    }

    const onMove = () => reposition()

    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)

    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [reposition, text])

  useLayoutEffect(() => {
    if (text) {
      reposition()
    }
  }, [reposition, text])

  // 菜单开着的时候，点浮窗外面就把它收起来。
  useEffect(() => {
    if (!openMenu) {
      return
    }

    const onPointerDown = (event: MouseEvent) => {
      if (!toolbarRef.current?.contains(event.target as Node)) {
        setOpenMenu(null)
      }
    }

    document.addEventListener('mousedown', onPointerDown, true)

    return () => document.removeEventListener('mousedown', onPointerDown, true)
  }, [openMenu])

  const payload = (): null | SelectionPayload => {
    const range = rangeRef.current

    return range && text ? { range, text } : null
  }

  if (!text) {
    return null
  }

  const menuAction = actions.find(action => action.id === openMenu)

  return createPortal(
    <div
      className="fixed z-50 flex flex-col items-start gap-1"
      data-testid={testId}
      onMouseDown={event => {
        // 点浮窗不许把选区点塌——浏览器 mousedown 的默认动作会把选区收走，
        // 那样「复制」按下去时选中的字已经没了。
        event.preventDefault()
      }}
      ref={toolbarRef}
      // 第一帧还没有落点（浮窗得先长出来才量得到它多大）⇒ 先藏着，量到了再摆位置。
      style={{
        left: anchor?.left ?? 0,
        top: anchor?.top ?? 0,
        visibility: anchor ? 'visible' : 'hidden'
      }}
    >
      <div className="flex items-center gap-0.5 rounded-lg border border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent)] p-0.5 shadow-md backdrop-blur-md">
        {actions.map(action => (
          <Button
            aria-expanded={action.menu?.length ? openMenu === action.id : undefined}
            aria-haspopup={action.menu?.length ? 'menu' : undefined}
            aria-label={action.label}
            className="h-6 gap-1 px-1.5 text-[0.7rem] leading-4"
            data-testid={`${testId}-${action.id}`}
            key={action.id}
            onClick={() => {
              if (action.menu?.length) {
                setOpenMenu(current => (current === action.id ? null : action.id))

                return
              }

              const current = payload()

              if (current) {
                action.onSelect?.(current)
              }

              hide()
            }}
            size="xs"
            type="button"
            variant="ghost"
          >
            {action.icon}
            {action.label}
            {action.menu?.length ? <ChevronDown className="size-3 opacity-70" /> : null}
          </Button>
        ))}
      </div>

      {menuAction?.menu?.length ? (
        <div
          className="max-h-64 min-w-40 overflow-y-auto rounded-lg border border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_96%,transparent)] p-1 shadow-md backdrop-blur-md"
          data-testid={`${testId}-menu`}
          role="menu"
        >
          {menuAction.menu.map(item => (
            <button
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs',
                'hover:bg-(--ui-control-active-background)'
              )}
              data-testid={`${testId}-menu-${item.id}`}
              key={item.id}
              onClick={() => {
                const current = payload()

                if (current) {
                  item.onSelect(current)
                }

                hide()
              }}
              role="menuitem"
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>,
    document.body
  )
}
