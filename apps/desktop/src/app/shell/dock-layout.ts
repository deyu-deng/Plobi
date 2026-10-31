// 左下图标条（卡片模式）的截断算法：宽度跟着左栏走，装不下的进「⋯」。
// 纯函数放在这里是为了能单测——组件只管量到的宽度喂进来。

/** 一个图标格：24px 控件 + 2px 间距（与 styles.css 里的 [data-dock-item] 对齐）。 */
export const DOCK_ICON_WIDTH = 26
/** 文字格（计时、上下文占比）没有图标可换，留短字，按 52px + 间距估。 */
export const DOCK_TEXT_WIDTH = 54
/** 「⋯」自己也要占一格。 */
export const DOCK_OVERFLOW_WIDTH = 26

export function dockItemWidth(item: { variant?: string }): number {
  return item.variant === 'text' ? DOCK_TEXT_WIDTH : DOCK_ICON_WIDTH
}

/**
 * 在 `available` 宽度里最多露出一格？装不下的那几格会换成「⋯」，
 * 所以回退时要把它自己那 28px 也算进去。
 */
export function dockFitCount(widths: readonly number[], available: number): number {
  let count = widths.length
  let total = widths.reduce((sum, width) => sum + width, 0)

  while (count > 0 && total + (count < widths.length ? DOCK_OVERFLOW_WIDTH : 0) > available) {
    count -= 1
    total -= widths[count] ?? 0
  }

  return Math.max(0, count)
}
