import { Fragment, type ReactNode } from 'react'
import { useStore } from '@nanostores/react'

import { $consoleAgents } from '@/app/console/store/agents'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $dossierPanelVisible, toggleDossierPanel } from '@/store/layout'

import type { ChatScope } from '../console/chat/scope'
import { secretaryShellLabel } from '../desktop-controller-utils'

import { cardDragProps } from './card-drag'
import { CardEdgeControlButtons, type CardEdgeControls } from './card-edge-controls'
import { PendingTicketsButton } from './pending-tickets'
import { TopBarIconButton } from './top-bar-icon-button'

/**
 * 对话卡自己的抬头（用户 10-31：对话界面太空，顶栏像 Qoder 一样显示当前 Agent 名）。
 * 左边是当前这摊的名字，右边是右栏开关——收起来的按钮住在卡里，不再跳回顶栏。
 * 名字那一块同时是这张卡的拖动把手：拖到右栏卡上两张互换（开关本身不参与拖动）。
 *
 * 右上角两枚图标（档案 / 待放行）共用 ``TopBarIconButton`` 一个组件，
 * 用户 11-10：「在它的图标旁边加一个图标，和档案一个形态，复用一个组件」。
 *
 * **那一列是注册表驱动的**（WP-DOSSIER-RAIL-UNIFY，用户 11-14 09:4x
 * 「我希望以后在这个地方添加的东西除了子内容的差异性，UI/UX 共用的是同一套组件」）：
 * 每一项的**形状**统一走 `TopBarIconButton`（入口那半句，11-10 做的），
 * 点开后的**内容**各写各的（浮层那半句，11-14 这把刀做的）。
 * **以后在这里加一个东西 = 加一条记录**，壳子不动。
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
 * 档案这一枚开合的是**浮在对话右上角的那张档案卡**（Qoder 的「任务监控」形态，
 * 用户 11-08 22:3x 实物截图后定的）：默认浮在对话上，卡内抬头右上角那枚**图钉**
 * 把它钉成对话右侧的一列。它**不再弹小窗**（用户 11-08 21:1x：「你的就像一个小
 * 弹窗，并没有完全逆向Qoder的形态」）。显隐口径在 store 里：对话卡全屏时默认显示，
 * 工作区在显示时默认收起，点它才展开；点过的那一次只在这一面算数（换了面回到默认）。
 * 档案卡本体在 `app/dossier/dossier-panel.tsx`，由控制器挂在对话卡里。
 */
export function chatTopBarRailEntries(deps: {
  dossierLabel: string
  dossierVisible: boolean
  edgeControls: CardEdgeControls
}): ChatTopBarRailEntry[] {
  return [
    {
      id: 'dossier',
      render: () => (
        <TopBarIconButton
          icon="book"
          label={deps.dossierLabel}
          onClick={() => toggleDossierPanel()}
          pressed={deps.dossierVisible}
        />
      )
    },
    { id: 'pending', render: () => <PendingTicketsButton /> },
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
  const dossierVisible = useStore($dossierPanelVisible)
  const level = scope.kind === 'agent' ? { l2: scope.id } : 'l1'
  const name = secretaryShellLabel(level, t.console.chiefSecretary, agents)

  const entries = chatTopBarRailEntries({
    dossierLabel: t.surface.agentDossier,
    dossierVisible,
    edgeControls: edgeControls ?? {}
  })

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
