import { useStore } from '@nanostores/react'

import {
  $chatCardNarrow,
  $monitorFace,
  $monitorPeekOpen,
  $monitorSlotVisible,
  toggleMonitorFace
} from '@/store/layout'

import type { MonitorFaceMeta } from './monitor-faces'
import { TopBarIconButton } from './top-bar-icon-button'

/**
 * 抬头右上角那一族三枚入口的**唯一组件**（档案／待放行／写脑待批都走它）。
 *
 * 用户 11-19 17:0x：「**三枚入口…UX也完全一致。要和现在档案的完全一致。**」
 * 「**完全复用同样的组件。不要写三套代码。**」
 *
 * **形状**在 `TopBarIconButton`（28px 方块、零描边、hover 淡底、角标、读屏标签）；
 * **行为**在 `toggleMonitorFace`（store 里那一份）：点哪一枚 ⇒ 那一张卡显示那一面，
 * 再点同一枚 ⇒ 关掉，点另一枚 ⇒ 换面。三枚入口的差别**只有登记册上那几行**。
 *
 * **按下态**也是一个口径：宽形态看「这张卡开着没有」，窄形态看「贴头浮层开着没有」，
 * 而且都还要**正是这一面**开着才算按下（开着的是另一面 ⇒ 这一枚不算按下）。
 */
export function MonitorEntryButton({ face }: { face: MonitorFaceMeta }) {
  const narrow = useStore($chatCardNarrow)
  const peekOpen = useStore($monitorPeekOpen)
  const visible = useStore($monitorSlotVisible)
  const current = useStore($monitorFace)

  const pressed = narrow ? peekOpen && current === face.id : visible && current === face.id

  return (
    <TopBarIconButton
      count={face.count}
      icon={face.icon}
      label={face.title}
      onClick={() => toggleMonitorFace(face.id)}
      pressed={pressed}
    />
  )
}
