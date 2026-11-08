import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { AgentDossierCard } from '@/app/chat/right-rail/agent-dossier-card'
import { $consoleAgents } from '@/app/console/store/agents'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

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
  const level = scope.kind === 'agent' ? { l2: scope.id } : 'l1'
  const name = secretaryShellLabel(level, t.console.chiefSecretary, agents)

  // 档案跟着 **Agent** 走，不跟着会话走：Plobi 没有「会话」这个概念，一个 Agent 就是
  // 一张嘴，同一个 Agent 的多轮对话共用同一份档案。L1 是总秘书——前端给的是作用域
  // 哨兵 `'l1'`，后端把它翻译成名册里 `role=l1_secretary` 那一行（名册是唯一真源）。
  const dossierAgentId = scope.kind === 'agent' ? scope.id : 'l1'
  const [dossierOpen, setDossierOpen] = useState(false)

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
        {/* 档案入口：对话界面右上角那一枚矢量图标（用户 11-07 拍板的新形态）——
            点它出档案卡片。**之前那枚挂在 `chat/index.tsx` 的 `ChatHeader` 上**，
            那一套壳已经不在路上了（console 用的是 `ChatSurface` 这一面），
            所以它一次都没在用户眼前出现过（用户 11-08 16:4x 问「档案的图标怎么
            还没有出现呀」）。这一处是真正在用的那一面。 */}
        <Popover onOpenChange={setDossierOpen} open={dossierOpen}>
          <PopoverTrigger asChild>
            <Button
              aria-label={t.surface.agentDossier}
              className={TOP_BAR_BUTTON}
              size="icon-xs"
              title={t.surface.agentDossier}
              type="button"
              variant="ghost"
            >
              <Codicon name="book" size="0.85rem" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-96 p-0" side="bottom" sideOffset={8}>
            <AgentDossierCard agentId={dossierAgentId} />
          </PopoverContent>
        </Popover>
        <CardEdgeControlButtons controls={edgeControls ?? {}} />
      </div>
    </div>
  )
}
