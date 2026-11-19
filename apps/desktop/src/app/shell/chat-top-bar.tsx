import { useStore } from '@nanostores/react'
import { Fragment, type ReactNode } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { DossierButton } from '@/app/dossier/dossier-panel'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { ChatScope } from '../console/chat/scope'
import { secretaryShellLabel } from '../desktop-controller-utils'

import { cardDragProps } from './card-drag'
import { CardEdgeControlButtons, type CardEdgeControls } from './card-edge-controls'
import { MindPendingButton } from './mind-pending-button'
import { PendingTicketsButton } from './pending-tickets'

/**
 * 对话卡自己的抬头（用户 10-31：对话界面太空，顶栏像 Qoder 一样显示当前 Agent 名）。
 * 左边是当前这摊的名字，右边是右栏开关——收起来的按钮住在卡里，不再跳回顶栏。
 * 名字那一块同时是这张卡的拖动把手：拖到右栏卡上两张互换（开关本身不参与拖动）。
 *
 * 右上角那三枚（档案／待放行／写脑待批）**走同一个组件**
 * （`monitor-entry-button.tsx`：同形状、同角标、同按下态、同一点击行为）——
 * 用户 11-19 17:0x：「**完全复用同样的组件。不要写三套代码。只是容器内部的信息和UI细节不同而已。**」
 *
 * **那一列是注册表驱动的**（WP-DOSSIER-RAIL-UNIFY，用户 11-14 09:4x
 * 「我希望以后在这个地方添加的东西除了子内容的差异性，UI/UX 共用的是同一套组件」）：
 * 每一项的**形状**统一走 `monitor-entry-button.tsx`，点开后的**内容**各写各的
 * （那一张卡里换的是内容）。**以后在这里加一个东西 = 加一条记录**，壳子不动。
 */

/** 那一列的一项。`render` 给出这一项自己的内容；外壳由渲染器统一。 */
export interface ChatTopBarRailEntry {
  id: string
  render: () => ReactNode
}

/**
 * 那一列的注册表。**纯函数** —— 测试能直接看它有几项、每项是什么。
 *
 * 档案跟着 **Agent** 走，不跟着会话走：Plobi 没有「会话」这个概念，一个 Agent 就是
 * 一张嘴，同一个 Agent 的多轮对话共用同一份档案。
 *
 * 三枚入口点开的**都是那一张卡**（`app/shell/monitor-region.tsx`），换的是
 * **这张卡显示哪一面**：档案／待放行／写脑待批。
 */
export function chatTopBarRailEntries(deps: {
  edgeControls: CardEdgeControls
}): ChatTopBarRailEntry[] {
  return [
    { id: 'dossier', render: () => <DossierButton /> },
    { id: 'pending', render: () => <PendingTicketsButton /> },
    // 写脑待批那一条（`WP-MIND-CONSENT-EVENTS`）：原来只在左栏底部角标与设置页里，
    // 用户 11-16 21:4x 拍定搬到对话卡（规格 §8 第 2 条）。只加这一条记录，壳子不动。
    { id: 'mind-pending', render: () => <MindPendingButton /> },
    { id: 'edge', render: () => <CardEdgeControlButtons controls={deps.edgeControls} /> }
  ]
}

export function ChatTopBar({
  edgeControls,
  scope
}: {
  /** 靠窗口边那张卡的右上角两枚符号（只有最外侧那张卡才有）。 */
  edgeControls?: CardEdgeControls
  scope: ChatScope
}) {
  const { t } = useI18n()
  const agents = useStore($consoleAgents)
  const level = scope.kind === 'agent' ? { l2: scope.id } : 'l1'
  const name = secretaryShellLabel(level, t.console.chiefSecretary, agents)

  const entries = chatTopBarRailEntries({ edgeControls: edgeControls ?? {} })

  return (
    <div className="flex h-(--card-header-height) shrink-0 select-none items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2.5 [-webkit-app-region:no-drag]">
      <div
        className={cn('flex h-full min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing')}
        data-chat-topbar=""
        title={name}
        {...cardDragProps('chat')}
      >
        <span className="min-w-0 truncate text-[0.8125rem] font-medium text-foreground">{name}</span>
      </div>
      <div className="ml-auto flex shrink-0 items-center">
        {entries.map(entry => (
          <Fragment key={entry.id}>{entry.render()}</Fragment>
        ))}
      </div>
    </div>
  )
}
