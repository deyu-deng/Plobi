import { type PointerEvent as ReactPointerEvent, useCallback, useRef } from 'react'

/**
 * **可拖动的浮层** —— 档案卡浮层那一件。
 *
 * Qoder 的任务监控浮层是 `fixed z-[1000] w-[280px]`，头部 `h-9` 且 `cursor-grab
 * active:cursor-grabbing`（**可拖动**），位置由 `clampTaskMonitorPosition` 夹在
 * 窗口内（`TASK_MONITOR_MARGIN=16`），拖完记住（11-18 10:0x 实物截图 ③）。
 *
 * 我们照抄这三件事：**拖得动**、**夹回窗口内**、**下次开还在原处**。
 */

export interface FloatPosition {
  x: number
  y: number
}

export interface FloatBounds {
  /** 浮层自身的高（用来算别让它掉出窗口下沿）。 */
  height: number
  /** 离窗口边至少留这么多。 */
  margin: number
  viewportHeight: number
  viewportWidth: number
  /** 浮层自身的宽。 */
  width: number
}

/**
 * 把浮层夹回窗口内（Qoder `clampTaskMonitorPosition` 的同义件）。
 *
 * 单独抽成纯函数是为了能钉住行为：夹不住就会拖到屏幕外找不回来。
 */
export function clampFloatPosition(position: FloatPosition, bounds: FloatBounds): FloatPosition {
  const maxX = Math.max(bounds.margin, bounds.viewportWidth - bounds.width - bounds.margin)
  const maxY = Math.max(bounds.margin, bounds.viewportHeight - bounds.height - bounds.margin)

  return {
    x: Math.min(Math.max(position.x, bounds.margin), maxX),
    y: Math.min(Math.max(position.y, bounds.margin), maxY)
  }
}

interface DragState {
  originX: number
  originY: number
  startX: number
  startY: number
}

/**
 * 拖动手柄的四个事件处理。挂在浮层的**头部**上（Qoder 也是头部可拖）。
 *
 * `position` 的语义是**相对定位父容器的 left/top**（像素），不是视口坐标 ——
 * 浮层默认停在右上角，第一次拖动时要把「当前位置」换算成父容器坐标系。
 */
export function useDraggableFloat(options: {
  /** 浮层自身的高（用来算别让它掉出窗口下沿）。 */
  height: number
  /** 离窗口边至少留这么多。 */
  margin: number
  onChange: (position: FloatPosition) => void
  position: FloatPosition | null
  /** 浮层自身的宽。 */
  width: number
}): {
  handleProps: {
    onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void
  }
} {
  const drag = useRef<DragState | null>(null)
  const { height, margin, onChange, position, width } = options

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const origin = position ?? originFromEvent(event)

      drag.current = {
        originX: origin.x,
        originY: origin.y,
        startX: event.clientX,
        startY: event.clientY
      }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    },
    [position]
  )

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const state = drag.current

      if (!state) {
        return
      }

      onChange(
        clampFloatPosition(
          {
            x: state.originX + (event.clientX - state.startX),
            y: state.originY + (event.clientY - state.startY)
          },
          {
            height,
            margin,
            viewportHeight: window.innerHeight,
            viewportWidth: window.innerWidth,
            width
          }
        )
      )
    },
    [height, margin, onChange, width]
  )

  const stop = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    drag.current = null
    event.currentTarget.releasePointerCapture?.(event.pointerId)
  }, [])

  return {
    handleProps: {
      onPointerCancel: stop,
      onPointerDown,
      onPointerMove,
      onPointerUp: stop
    }
  }
}

/**
 * 第一次拖动时的起点：浮层**此刻**相对定位父容器的左上角。
 *
 * 浮层还没拖过时是停在默认位置（右上角，`right`/`top` 写的），拿不到像素值 ⇒
 * 从它的实际 rect 反算，而不是拿指针位置当左上角（那样会跳一下）。
 */
function originFromEvent(event: ReactPointerEvent<HTMLElement>): FloatPosition {
  const float = event.currentTarget.closest('[data-testid="dossier-panel"]') as HTMLElement | null
  const parent = float?.parentElement

  if (!float || !parent) {
    return { x: event.clientX, y: event.clientY }
  }

  const floatRect = float.getBoundingClientRect()
  const parentRect = parent.getBoundingClientRect()

  return { x: floatRect.left - parentRect.left, y: floatRect.top - parentRect.top }
}
