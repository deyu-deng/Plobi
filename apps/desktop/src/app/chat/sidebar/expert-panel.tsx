import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { SidebarSectionHeader } from './chrome'
import { elapsedText, getAgentExperts, getExpertLog, goalLine, logLine } from './expert-runs'
import type { ExpertLogMessage, ExpertRun, ExpertRunsData } from './expert-runs'

/**
 * 左栏那一格「专家」（WP-L3-EXPERTS，用户 10-28 口径）：
 * 临时专家**不进名册**，但 L2 一派出去，这里就出现一行；点开是它自己的**工作日志**。
 *
 * 名单与日志都读同一个状态库：专家就是一条 `source=subagent` 的子会话，
 * 日志复用 `/api/sessions/:id/messages`。正在干的时候每 3.5 秒自读。
 * 没派过整格就不出现——屏上不放句子。
 */
export function ExpertPanel({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const c = t.console
  const [data, setData] = useState<null | ExpertRunsData>(null)
  const [open, setOpen] = useState<null | ExpertRun>(null)
  const [show, setShow] = useState(true)

  useEffect(() => {
    let cancelled = false

    const load = (first: boolean) => {
      if (first) {
        setData(null)
      }

      getAgentExperts(agentId)
        .then(body => {
          if (!cancelled) {
            setData(body?.data ?? { experts: [] })
          }
        })
        .catch(() => {
          if (!cancelled && first) {
            setData({ experts: [] })
          }
        })
    }

    load(true)
    const timer = setInterval(() => load(false), 3500)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId])

  const experts = data?.experts ?? []

  if (experts.length === 0) {
    return null
  }

  const running = experts.filter(expert => expert.status === 'running').length

  return (
    <div className="flex flex-col gap-px">
      <SidebarSectionHeader
        label={c.expertsTitle}
        meta={running > 0 ? `${running}/${experts.length}` : experts.length}
        onToggle={() => setShow(value => !value)}
        open={show}
      />

      {show &&
        experts.map(expert => (
          <button
            className="grid min-h-[1.625rem] grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-1.5 rounded-md pl-2 pr-1 text-left hover:bg-(--ui-bg-secondary)"
            key={expert.id}
            onClick={() => setOpen(expert)}
            title={expert.goal}
            type="button"
          >
            <span
              className={cn(
                'size-1.5 rounded-full',
                expert.status === 'running' ? 'animate-pulse bg-(--ui-accent)' : 'bg-(--ui-stroke-tertiary)'
              )}
            />
            <RoleIcon role={expert.role} />
            <span className="min-w-0 truncate text-[0.8125rem] leading-none text-(--ui-text-secondary)">
              {goalLine(expert)}
            </span>
            <span className="flex items-center gap-1 text-[0.62rem] text-(--ui-text-quaternary)">
              {expert.status === 'running' && elapsedText(expert)}
              {expert.tool_calls > 0 && (
                <span className="flex items-center gap-0.5">
                  <Codicon name="tools" size="0.6875rem" />
                  {expert.tool_calls}
                </span>
              )}
            </span>
          </button>
        ))}

      {open && <ExpertLog expert={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

const ROLE_ICONS: Record<string, { icon: string, label: 'expertRoleBrowser' | 'expertRoleComputer' | 'expertRoleFile' | 'expertRoleSoftware' }> = {
  browser: { icon: 'globe', label: 'expertRoleBrowser' },
  computer: { icon: 'device-desktop', label: 'expertRoleComputer' },
  file: { icon: 'file', label: 'expertRoleFile' },
  software: { icon: 'terminal', label: 'expertRoleSoftware' }
}

function RoleIcon({ role }: { role?: string }) {
  const { t } = useI18n()
  const spec = role ? ROLE_ICONS[role] : undefined

  if (!spec) {
    return <span className="size-3.5" />
  }

  return (
    <span className="grid size-3.5 place-items-center text-(--ui-text-tertiary)" title={t.console[spec.label]}>
      <Codicon name={spec.icon} size="0.8125rem" />
    </span>
  )
}

function ExpertLog({ expert, onClose }: { expert: ExpertRun, onClose: () => void }) {
  const { t } = useI18n()
  const c = t.console
  const [messages, setMessages] = useState<null | ExpertLogMessage[]>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    const load = () => {
      getExpertLog(expert.id)
        .then(body => {
          if (!cancelled) {
            setMessages(body?.messages ?? [])
          }
        })
        .catch(() => {
          if (!cancelled) {
            setError(c.expertsLoadFailed)
          }
        })
    }

    load()
    const timer = expert.status === 'running' ? setInterval(load, 2000) : undefined

    return () => {
      cancelled = true

      if (timer) {
        clearInterval(timer)
      }
    }
  }, [expert.id, expert.status, c.expertsLoadFailed])

  // 第一条用户消息就是任务书本身；标题已经写着同一句话，不再在正文里占一大块。
  const lines = (messages ?? []).slice(1)
  const ready = messages !== null && lines.length === 0 && !error

  return (
    <Dialog onOpenChange={next => { if (!next) {onClose()} }} open>
      <DialogContent className="flex max-h-[70vh] max-w-xl flex-col gap-2">
        <DialogHeader>
          <DialogTitle className="truncate text-[0.8125rem]">{goalLine(expert)}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-[160px] flex-1 flex-col gap-1 overflow-y-auto">
          {messages === null && !error && (
            <span className="text-[0.72rem] text-(--ui-text-tertiary)">{c.expertsLoading}</span>
          )}
          {error && <span className="text-[0.72rem] text-(--ui-red)">{error}</span>}
          {ready && <span className="text-[0.72rem] text-(--ui-text-tertiary)">{c.expertsEmpty}</span>}
          {lines.map((message, index) => {
            const line = logLine(message)

            if (!line.text) {
              return null
            }

            return (
              <div
                className={cn(
                  'whitespace-pre-wrap text-[0.74rem] leading-snug',
                  line.kind === 'tool'
                    ? 'font-mono text-[0.68rem] text-(--ui-text-quaternary)'
                    : message.role === 'user'
                      ? 'text-(--ui-text-tertiary)'
                      : 'text-(--ui-text)'
                )}
                key={`${message.role}-${index}`}
              >
                {line.text.length > 1200 ? `${line.text.slice(0, 1200)}…` : line.text}
              </div>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
