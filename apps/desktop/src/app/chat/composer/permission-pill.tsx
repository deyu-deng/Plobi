import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { setSessionApproval } from '@/lib/yolo-session'
import { notify } from '@/store/notifications'
import {
  $activeSessionId,
  $approvalMode,
  type ApprovalMode,
  approvalModeForAgent,
  rememberApprovalMode,
  setApprovalModePending
} from '@/store/session'

import { agentScopeKey } from './queue-key'

const PILL = cn(
  'h-(--composer-control-size) max-w-48 shrink-0 gap-1 rounded-md px-2 text-xs font-normal',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)

const MODE_ICON: Record<ApprovalMode, string> = {
  manual: 'shield',
  smart: 'play-circle',
  off: 'warning'
}

/**
 * 输入框里的权限档（用户 10-31：YOLO 变 Qoder 那种三档下拉）。
 * 每次询问 = manual，自动审批 = smart，完全访问 = off（旧 YOLO）。
 * 档位是会话级的：命令行/TUI/排程读的仍是全局 approvals.mode。
 */
export function PermissionPill({ disabled }: { disabled?: boolean }) {
  const { t } = useI18n()
  const copy = t.composer.permission
  const mode = useStore($approvalMode)
  const sessionId = useStore($activeSessionId)
  const { requestGateway } = useGatewayRequest()
  const [open, setOpen] = useState(false)
  // 那一档归**Agent**，不归会话（用户 11-21：「每个 Agent 各记各的」）。
  const agentKey = agentScopeKey(useLocation().pathname)

  const options: Array<{ detail: string; id: ApprovalMode; label: string }> = [
    { detail: copy.askDetail, id: 'manual', label: copy.ask },
    { detail: copy.autoDetail, id: 'smart', label: copy.auto },
    { detail: copy.fullDetail, id: 'off', label: copy.full }
  ]

  const current = options.find(option => option.id === mode) ?? options[0]

  // 打开一个 Agent ⇒ 把**这个 Agent 记住的那一档**推给这条会话（新建的 Agent 没有
  // 自己那一档 ⇒ 落在你上次选的那一档）。
  //
  // 为什么每次打开都要推：那一档在网关那边只活在**会话**上（进程内存），而归属权
  // 在 Agent ⇒ 不推的话，打开一个 Agent 看到的就是全局兜底（每次询问）。
  useEffect(() => {
    if (!sessionId) {
      return
    }

    const wanted = approvalModeForAgent(agentKey)

    void setSessionApproval(requestGateway, sessionId, wanted).catch(() => undefined)
  }, [agentKey, requestGateway, sessionId])

  const choose = (next: ApprovalMode) => {
    setOpen(false)

    if (next === mode) {
      return
    }

    // 用户自己的动作 ⇒ 写记忆（这个 Agent 一份 ＋ 「上次那档」一份）。
    rememberApprovalMode(agentKey, next)

    // 新对话草稿还没有运行时会话：先记住，建好会话后在 use-session-actions 里补交。
    if (!sessionId) {
      setApprovalModePending(true)

      return
    }

    void setSessionApproval(requestGateway, sessionId, next).catch(() => {
      notify({ kind: 'error', title: copy.label, message: copy.failed })
    })
  }

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <Tip label={copy.label}>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={copy.label}
            className={cn(
              PILL,
              'inline-flex items-center',
              mode === 'off' && 'text-destructive hover:text-destructive'
            )}
            disabled={disabled}
            type="button"
          >
            <Codicon name={MODE_ICON[mode]} size="0.8125rem" />
            <span className="truncate">{current.label}</span>
            <Codicon className="opacity-50" name="chevron-down" size="0.75rem" />
          </button>
        </DropdownMenuTrigger>
      </Tip>
      <DropdownMenuContent align="start" className="w-80 p-1" side="top" sideOffset={8}>
        {options.map(option => {
          const active = option.id === mode

          return (
            <DropdownMenuItem
              className={cn(
                'flex items-start gap-2 rounded-md px-2 py-2',
                option.id === 'off' ? 'text-destructive data-[highlighted]:text-destructive' : 'text-foreground'
              )}
              key={option.id}
              onSelect={() => choose(option.id)}
            >
              <Codicon className="mt-0.5 shrink-0" name={MODE_ICON[option.id]} size="0.875rem" />
              <span className="min-w-0 flex-1">
                <span className="block text-[0.8rem] font-medium">{option.label}</span>
                <span className="mt-0.5 block text-[0.7rem] leading-snug opacity-70">{option.detail}</span>
              </span>
              {active ? <Codicon className="mt-0.5 shrink-0" name="check" size="0.875rem" /> : null}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
