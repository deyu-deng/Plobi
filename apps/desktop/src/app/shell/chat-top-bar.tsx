import { useStore } from '@nanostores/react'

import { $consoleAgents } from '@/app/console/store/agents'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $dossierPanelVisible, toggleDossierPanel } from '@/store/layout'

import type { ChatScope } from '../console/chat/scope'
import { secretaryShellLabel } from '../desktop-controller-utils'

import { cardDragProps } from './card-drag'
import { CardEdgeControlButtons, type CardEdgeControls } from './card-edge-controls'

const TOP_BAR_BUTTON =
  'grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'

/**
 * 对话卡自己的抬头（用户 10-31：对话界面太空，顶栏像 Qoder 一样显示当前 Agent 名）。
 * 左边是当前这摊的名字，右边是右栏开关——收起来的按钮住在卡里，不再跳回顶栏。
 * 名字那一块同时是这张卡的拖动把手：拖到右栏卡上两张互换（开关本身不参与拖动）。
 */
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

  // 档案跟着 **Agent** 走，不跟着会话走：Plobi 没有「会话」这个概念，一个 Agent 就是
  // 一张嘴，同一个 Agent 的多轮对话共用同一份档案。
  //
  // 这一枚图标现在**不再弹小窗**（用户 11-08 21:1x：「你的就像一个小弹窗，并没有完全
  // 逆向Qoder的形态」）：它开合的是**对话卡右侧那条长卡**（Qoder 的 Fixed 形态）。
  // 显隐口径：对话卡全屏展开时默认显示，工作区在显示时默认收起，点它才展开。
  // 档案卡本体在 `app/dossier/dossier-panel.tsx`，由控制器挂在对话卡右侧。

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
        <Button
          aria-label={t.surface.agentDossier}
          aria-pressed={dossierVisible}
          className={TOP_BAR_BUTTON}
          onClick={() => toggleDossierPanel()}
          size="icon-xs"
          title={t.surface.agentDossier}
          type="button"
          variant="ghost"
        >
          <Codicon name="book" size="0.85rem" />
        </Button>
        <CardEdgeControlButtons controls={edgeControls ?? {}} />
      </div>
    </div>
  )
}
