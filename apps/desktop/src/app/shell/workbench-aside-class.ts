import { cn } from '@/lib/utils'

/**
 * 工作面卡（对话右侧那条轨）自己的外框类名。
 *
 * **顶栏让位跟形态走，不跟位置走**（用户 11-10：「顶栏的高度还会变化」）：
 * 那 34px 的 `pt-(--titlebar-height)` 是**右栏形态**要的——顶栏是浮层（absolute），
 * 右栏那条轨的顶边在 y=0，卡要自己把顶栏那一条让出来。
 *
 * 卡片模式（`shellCards`，默认开）里画布自己已经让过（`styles.css:2028`
 * `padding-top: calc(var(--titlebar-height) + 6px)`）⇒ 右栏形态里那 34px 由
 * `styles.css:2147-2156` 的 `padding-top: 0 !important` 兜掉；
 * 可**主区**不在那三条 `[data-pane-id=…]` 规则里 ⇒ 工作面卡被最大化进主区后，
 * 这 34px 没人兜 ⇒ **卡面顶部多出一条 34px 的空带** ⇒ 顶栏（抬头那条）看着变高了。
 *
 * ⇒ 卡片模式一律不让位；非卡片模式（顶栏是浮层）才让。
 *
 * 互换态（`swapped`）工作面卡也在主区：那一档本来就不带描边与让位，形状保持原样。
 */
export function resolveWorkbenchAsideClass({
  panesFlipped,
  shellCards,
  swapped
}: {
  /** 左右互换过没有（换了边，工作面卡那条轨换到左边）。 */
  panesFlipped: boolean
  /** 卡片模式（画布自己让过顶栏）。 */
  shellCards: boolean
  /** 对话卡 ↔ 工作面卡互换（工作面卡进主区）。 */
  swapped: boolean
}): string {
  return cn(
    'relative flex h-full w-full min-w-0 flex-col overflow-hidden text-(--ui-text-tertiary)',
    swapped
      ? 'bg-(--ui-sidebar-surface-background)'
      : cn(
          'border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background)',
          !shellCards && 'pt-(--titlebar-height)',
          panesFlipped
            ? 'border-r shadow-[inset_-0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
            : 'border-l shadow-[inset_0.0625rem_0_0_color-mix(in_srgb,white_18%,transparent)]'
        )
  )
}
