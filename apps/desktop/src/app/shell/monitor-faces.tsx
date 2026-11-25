import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { useI18n } from '@/i18n'
import { $mindPendingCount } from '@/store/mind-pending'
import { $pendingInboxCount } from '@/store/pending-inbox'

import { chatCardFaceDefsFor, type ChatCardFaceDef } from './slot-registry'
import { useSlotRegistry } from './slot-registry-context'

/**
 * 对话卡右上角那一列的**登记册**——**从 slot 声明表派生**（裁定 239 第四刀）。
 *
 * 原来这里是**一张手写的表**（三行：档案／待放行／写脑待批）⇒ 插件加不进那一列，
 * 用户也关不掉其中一枚。现在清单在声明表里（`DEFAULT_SLOT_DECLARATION` 的
 * chat-card 那三条）：插件能**替换**其中一枚、也能自己**加**一枚，用户关掉的
 * **真的不在**（不是藏起来）。
 *
 * 这一档只管**每一面叫什么、图标是哪一枚、角标上几个数、显示哪一页**；
 * 壳（卡、头、行、按钮）全在 `monitor-panel.tsx`／`monitor-entry-button.tsx`，
 * 内容（那一面自己的分区与行）在各自的文件里。
 *
 * **为什么登记册不 import 那三份内容**：内容要 import 这一档（拿自己的标题与图标），
 * 反过来再 import 就成环了。所以这里只有「叫什么」，内容由 `monitor-region.tsx` 接线。
 */
export type MonitorFaceMeta = ChatCardFaceDef & {
  /** 角标上的数（0 与 undefined 都不画角标）。 */
  count?: number
}

/**
 * 那一列现在有哪些面。
 *
 * **角标那两格**：声明说一声「这一格的数从哪儿来」（`badge`）⇒ 数由**宿主**给
 * （**角标属于那一格，不属于谁填它**——插件换了那一枚的图标，角标还在）。
 */
export function useMonitorFaceMetas(): MonitorFaceMeta[] {
  const { t } = useI18n()
  const registry = useSlotRegistry()

  // 角标那两个数：宿主自己的快照。
  const pendingCount = useStore($pendingInboxCount)
  const mindPendingCount = useStore($mindPendingCount)

  const defs = useMemo(() => chatCardFaceDefsFor(registry, t), [registry, t])

  return useMemo<MonitorFaceMeta[]>(
    () =>
      defs.map(def => ({
        ...def,
        count:
          def.badge === 'pending-inbox'
            ? pendingCount
            : def.badge === 'mind-pending'
              ? mindPendingCount
              : undefined
      })),
    [defs, pendingCount, mindPendingCount]
  )
}
