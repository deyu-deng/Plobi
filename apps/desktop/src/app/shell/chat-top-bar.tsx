import { useStore } from '@nanostores/react'
import { Fragment, type ReactNode } from 'react'

import { $consoleAgents } from '@/app/console/store/agents'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { ChatScope } from '../console/chat/scope'
import { secretaryShellLabel } from '../desktop-controller-utils'

import { cardDragProps } from './card-drag'
import { CardEdgeControlButtons, type CardEdgeControls } from './card-edge-controls'
import { MonitorEntryButton } from './monitor-entry-button'
import { useMonitorFaceMetas, type MonitorFaceMeta } from './monitor-faces'
import { usePendingInboxPolling } from './pending-tickets'

/**
 * 对话卡自己的抬头（用户 10-31：对话界面太空，顶栏像 Qoder 一样显示当前 Agent 名）。
 * 左边是当前这摊的名字，右边是右栏开关——收起来的按钮住在卡里，不再跳回顶栏。
 * 名字那一块同时是这张卡的拖动把手：拖到右栏卡上两张互换（开关本身不参与拖动）。
 *
 * 右上角那三枚（档案／待放行／写脑待批）**从 slot 声明表派生**（裁定 239 第四刀）：
 * 一枚 chat-card slot ⇒ 一枚 `MonitorEntryButton`（**同一个组件**：同形状、同角标、
 * 同按下态、同一点击行为——用户 11-19 17:0x：「**完全复用同样的组件。不要写三套代码。**」）。
 *
 * **那一列是注册表驱动的**（WP-DOSSIER-RAIL-UNIFY，用户 11-14 09:4x
 * 「我希望以后在这个地方添加的东西除了子内容的差异性，UI/UX 共用的是同一套组件」）：
 * 每一项的**形状**统一走 `monitor-entry-button.tsx`，点开后的**内容**各写各的
 * （那一张卡里换的是内容）。**以后在这里加一个东西 = 往声明表里加一条**，
 * 壳子不动——插件带来的那一枚也是这样来的（用户 11-25 19:5x 拍定）。
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
 *
 * **档案那一枚只在首席（L2）那屏出现**（用户 11-19 22:3x：「**指挥那一栏也不要出现
 * 当个档案图标了**」）：指挥没有档案——五种档案只归首席（名册里 `role=conductor`
 * 那一行是指挥，`role=principal` 那些是首席）。给指挥一枚档案入口，就是指着一份
 * 不存在的档案。**这一条现在是声明里的一句 `shells: ['l2']`**，不是代码里的 if。
 */
export function chatTopBarRailEntries(deps: {
  edgeControls: CardEdgeControls
  /** 当前这一屏是**谁**：L1 ＝ 指挥，L2 ＝ 某个首席。 */
  scope: ChatScope
  /** 那一列有哪些面（**从 slot 声明表派生**）。 */
  faces: MonitorFaceMeta[]
}): ChatTopBarRailEntry[] {
  const shell = deps.scope.kind === 'agent' ? 'l2' : 'l1'
  const faceEntries = deps.faces
    .filter(face => face.shells.includes(shell))
    .map(face => ({ id: face.id, render: () => <MonitorEntryButton face={face} /> }))

  return [
    ...faceEntries,
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

  // 那一列从 slot 声明表派生（插件带来的那一枚会自动出现——用户 11-25 19:5x 拍定）。
  const faces = useMonitorFaceMetas()

  // 角标上那个数要**面板关着的时候**也准 ⇒ 那一格在的时候，轮询住在这里。
  usePendingInboxPolling(faces.some(face => face.badge === 'pending-inbox'))

  const entries = chatTopBarRailEntries({ edgeControls: edgeControls ?? {}, scope, faces })

  return (
    <>
      {/*
       * 这一行横跨**整卡**（对话区 ＋ 并排列那一列），不是对话区那一列的一条。
       *
       * 用户 11-19 22:50x：「**这几个图标还是没有放到卡片的最右边呀。**」
       * —— 那三枚入口本来住在这一行里、用 `ml-auto` 贴**它自己那一列**的右缘；
       * 并排列钉开时对话区被挤窄 ⇒ 三枚图标就停在并排列**左边**，离卡片最右边差了整整一列。
       *
       * ⇒ 这一行**脱离对话区那一列的流**，绝对定位在**卡片**上（包含块＝对话卡本体，
       * `desktop-controller.tsx` 的 `chatCard` 是 `relative`，中间没有别的定位祖先）：
       * 这一行跨满整卡 ⇒ 那三枚入口（`ml-auto`）落在**整卡**的右缘。
       *
       * 上一把刀（`WP-QODER-MONITOR-UI`）把**那道线**接过去了（并排列那一条空条
       * `MonitorTopBarRow`）⇒ 顶栏看着横跨整卡；这一刀把**内容**也接过去。
       */}
      <div
        className="absolute inset-x-0 top-0 z-20 flex h-(--card-header-height) shrink-0 select-none items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2.5 [-webkit-app-region:no-drag]"
        data-chat-topbar-row=""
      >
        <div
          className={cn('flex h-full min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing')}
          data-chat-topbar=""
          title={name}
          {...cardDragProps('chat')}
        >
          <span className="min-w-0 truncate text-[0.8125rem] font-medium text-foreground">{name}</span>
        </div>
        <div className="ml-auto flex shrink-0 items-center" data-chat-topbar-actions="">
          {entries.map(entry => (
            <Fragment key={entry.id}>{entry.render()}</Fragment>
          ))}
        </div>
      </div>
      {/* 占位：这一行不在流里 ⇒ 下面那一块自己让出它的高度（原来这一行就在流里）。 */}
      <div aria-hidden className="h-(--card-header-height) shrink-0" data-chat-topbar-spacer="" />
    </>
  )
}
