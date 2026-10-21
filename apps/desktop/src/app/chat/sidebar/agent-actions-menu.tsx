import { useState } from 'react'

import { dissolveAgent } from '@/app/console/api'
import { refreshConsoleAgents } from '@/app/console/store/agents'
import type { Agent } from '@/app/console/types'
import { Codicon } from '@/components/ui/codicon'
import { writeClipboardText } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { notify, notifyError } from '@/store/notifications'

/**
 * 左栏每个 Agent 行旁边的「三个点」——`WP-AGENT-MENU`（10-21 接手那一格）。
 *
 * 范围**三件**（不新造 REST，全走既有嘴）：
 *   ① 复制 ID ——纯前端，抄 `agent.id` 那个 slug（用户拿它去命令行/别处指认
 *      这个 Agent）；
 *   ② 进工作台 ——复用行上已有的 `onNavigateAgent`（左栏点一下就是同一件事；
 *      菜单里再给一格是给习惯用菜单的人）；
 *   ③ 归档 ——接既有那个 dissolve 端点。**L1 秘书由端点自己回 400**：面上要
 *      看得见那句拒话，不许在菜单里静默禁用它。
 *
 * 这一格只放**这个 Agent 自己**的事。profile 那个概念已退役（裁定 180 之后
 * 只有一份总账、Agent 自己就是身份），菜单里不许再冒出它。
 */
export interface AgentActionsMenuProps {
  agent: Agent
  /** 打开这个 Agent 的对话（左栏行本身点一下也是同一件事）。 */
  onNavigate: (agentId: string) => void
}

export function AgentActionsMenu({ agent, onNavigate }: AgentActionsMenuProps) {
  const { t } = useI18n()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const label = agent.name || agent.id

  const copyId = async () => {
    try {
      await writeClipboardText(agent.id)
      notify({ message: t.console.agentMenuCopied })
    } catch (cause) {
      notifyError(cause, t.console.agentMenuCopyFailed)
    }
  }

  const archive = async () => {
    setBusy(true)

    try {
      await dissolveAgent(agent.id)
      await refreshConsoleAgents()
      setConfirmOpen(false)
      notify({ message: t.console.agentMenuArchived })
    } catch (cause) {
      // 端点自己回的那句拒话（L1 秘书不许归档等）要原样到用户眼前——不许
      // 换成一句笼统的「失败」。
      notifyError(cause, t.console.agentMenuArchiveFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={t.console.agentMenuMoreFor(label)}
            className="flex size-5 shrink-0 items-center justify-center rounded-md text-(--ui-text-tertiary) opacity-0 transition-opacity group-hover:opacity-100 hover:bg-(--ui-control-hover-background) hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100"
            onClick={event => event.stopPropagation()}
            type="button"
          >
            <Codicon name="ellipsis" size="0.75rem" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" aria-label={t.console.agentMenuMoreFor(label)} className="w-52" sideOffset={6}>
          <DropdownMenuItem
            onSelect={() => {
              void copyId()
            }}
          >
            {t.console.agentMenuCopyId}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              onNavigate(agent.id)
            }}
          >
            {t.console.agentMenuOpen}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setConfirmOpen(true)
            }}
            variant="destructive"
          >
            {t.console.agentMenuArchive}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog onOpenChange={setConfirmOpen} open={confirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.console.agentMenuArchiveConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.console.agentMenuArchiveConfirmBody.replace('{name}', label)}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-(--ui-control-hover-background)"
              disabled={busy}
              onClick={() => setConfirmOpen(false)}
              type="button"
            >
              {t.console.agentMenuArchiveCancel}
            </button>
            <button
              className="rounded-md bg-destructive px-3 py-1.5 text-sm text-destructive-foreground disabled:opacity-60"
              disabled={busy}
              onClick={() => {
                void archive()
              }}
              type="button"
            >
              {t.console.agentMenuArchiveConfirmAction}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
