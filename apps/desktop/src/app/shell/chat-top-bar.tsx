import { useStore } from '@nanostores/react'

import { $consoleAgents } from '@/app/console/store/agents'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { ChatScope } from '../console/chat/scope'
import { secretaryShellLabel } from '../desktop-controller-utils'

import { cardDragProps } from './card-drag'

/**
 * 对话卡自己的抬头（用户 10-31：对话界面太空，顶栏像 Qoder 一样显示当前 Agent 名）。
 * 左边是当前这摊的名字，右边是右栏开关——收起来的按钮住在卡里，不再跳回顶栏。
 * 名字那一块同时是这张卡的拖动把手：拖到右栏卡上两张互换（开关本身不参与拖动）。
 */
export function ChatTopBar({
  rightRail,
  scope
}: {
  rightRail?: { open: boolean; toggle: () => void }
  scope: ChatScope
}) {
  const { t } = useI18n()
  const agents = useStore($consoleAgents)
  const level = scope.kind === 'agent' ? { l2: scope.id } : 'l1'
  const name = secretaryShellLabel(level, t.console.chiefSecretary, agents)
  const label = rightRail?.open ? t.titlebar.hideRightSidebar : t.titlebar.showRightSidebar

  return (
    <div className="flex h-(--titlebar-height) shrink-0 select-none items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2.5 [-webkit-app-region:no-drag]">
      <div
        className={cn('flex h-full min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing')}
        data-chat-topbar=""
        title={name}
        {...cardDragProps('chat')}
      >
        <span className="min-w-0 truncate text-[0.8125rem] font-medium text-foreground">{name}</span>
      </div>
      {rightRail ? (
        <Tip label={label}>
          <Button
            aria-label={label}
            aria-pressed={rightRail.open}
            onClick={rightRail.toggle}
            size="icon-xs"
            type="button"
            variant="ghost"
          >
            <Codicon name="layout-sidebar-right" size="0.875rem" />
          </Button>
        </Tip>
      ) : null}
    </div>
  )
}
