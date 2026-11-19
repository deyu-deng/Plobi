import { useStore } from '@nanostores/react'

import { useI18n } from '@/i18n'
import type { MonitorFaceId } from '@/store/layout'
import { $mindPendingCount } from '@/store/mind-pending'
import { $pendingInboxCount } from '@/store/pending-inbox'

/**
 * 这一族三面（档案／待放行／写脑待批）的**登记册**。
 *
 * 用户 11-19 17:0x：「**完全复用同样的组件。不要写三套代码。只是容器内部的信息和UI细节不同而已。**」
 *
 * 这一档只管**每一面叫什么、图标是哪一枚、角标上几个数**；
 * 壳（卡、头、行、按钮）全在 `monitor-panel.tsx`／`monitor-entry-button.tsx`，
 * 内容（那一面自己的分区与行）在各自的文件里。
 *
 * **为什么登记册不 import 那三份内容**：内容要 import 这一档（拿自己的标题与图标），
 * 反过来再 import 就成环了。所以这里只有「叫什么」，内容由 `monitor-region.tsx` 接线。
 */
export interface MonitorFaceMeta {
  id: MonitorFaceId
  /** Codicon 的图标名（卡头那一枚与入口那一枚共用同一个名 ⇒ 两处永远同一个形）。 */
  icon: string
  /** 这一面叫什么（屏上只写「这是什么」）。 */
  title: string
  /** 角标上的数（0 与 undefined 都不画角标）。 */
  count?: number
}

/** 三面的登记册（顺序＝抬头右上角那三枚的顺序）。 */
export function useMonitorFaceMetas(): MonitorFaceMeta[] {
  const { t } = useI18n()
  const pendingCount = useStore($pendingInboxCount)
  const mindPendingCount = useStore($mindPendingCount)

  return [
    { id: 'dossier', icon: 'book', title: t.surface.agentDossier },
    { id: 'pending', icon: 'inbox', title: t.surface.pendingTickets, count: pendingCount },
    { id: 'mind-pending', icon: 'checklist', title: t.mindPending.title, count: mindPendingCount }
  ]
}

/** 只要一面的登记（那一面自己的入口用）。 */
export function useMonitorFace(id: MonitorFaceId): MonitorFaceMeta {
  const metas = useMonitorFaceMetas()

  return metas.find(meta => meta.id === id) ?? metas[0]
}
