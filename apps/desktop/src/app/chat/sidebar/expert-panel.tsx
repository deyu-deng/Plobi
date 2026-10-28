import { useEffect, useState } from 'react'

import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { elapsedText, getAgentExperts, getExpertLog, goalLine, logLine } from './expert-runs'
import type { ExpertLogMessage, ExpertRun, ExpertRunsData } from './expert-runs'

/**
 * 左栏那一格「专家」（WP-L3-EXPERTS，用户 10-28 口径）：
 * 临时专家**不进名册**，但 L2 一派出去，这里就出现一行；点开是它自己的**工作日志**。
 *
 * 名单与日志都读同一个状态库：专家就是一条 `source=subagent` 的子会话，
 * 日志复用 `/api/sessions/:id/messages`。正在干的时候每 3.5 秒自读。
 */
export function ExpertPanel({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const c = t.console
  const [data, setData] = useState<null | ExpertRunsData>(null)
  const [open, setOpen] = useState<null | ExpertRun>(null)

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
            setData({ experts: [], reason: c.expertsLoadFailed })
          }
        })
    }

    load(true)
    const timer = setInterval(() => load(false), 3500)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId, c.expertsLoadFailed])

  const experts = data?.experts ?? []
  const running = experts.filter(expert => expert.status === 'running').length

  const roleLabels: Record<string, string> = {
    browser: c.expertRoleBrowser,
    computer: c.expertRoleComputer,
    file: c.expertRoleFile,
    software: c.expertRoleSoftware
  }

  return (
    <div className="flex flex-col gap-px">
      <span className="flex items-center gap-1 px-1 text-[0.7rem] uppercase text-(--ui-text-tertiary)">
        <span className="min-w-0 flex-1 truncate">{c.expertsTitle}</span>
        {running > 0 && (
          <span className="shrink-0 text-[0.62rem] font-normal normal-case text-(--ui-accent)">
            {c.expertsRunning} {running}
          </span>
        )}
      </span>

      {data === null && (
        <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
          {c.expertsLoading}
        </div>
      )}

      {data !== null && experts.length === 0 && (
        <div className="px-3 pb-1 text-[0.75rem] leading-snug text-(--ui-text-tertiary)">
          {data.reason || c.expertsEmpty}
        </div>
      )}

      {experts.map(expert => (
        <button
          className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-(--ui-bg-secondary)"
          key={expert.id}
          onClick={() => setOpen(expert)}
          title={expert.goal}
          type="button"
        >
          <span
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              expert.status === 'running' ? 'animate-pulse bg-(--ui-accent)' : 'bg-(--ui-stroke-tertiary)'
            )}
          />
          {expert.role && (
            <span className="shrink-0 rounded bg-(--ui-bg-secondary) px-1 text-[0.6rem] text-(--ui-text-tertiary)">
              {roleLabels[expert.role] ?? expert.role}
            </span>
          )}
          <span className="min-w-0 flex-1 truncate text-[0.75rem]">{goalLine(expert)}</span>
          <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
            {elapsedText(expert)}
            {expert.tool_calls > 0 ? ` · ${expert.tool_calls}` : ''}
          </span>
        </button>
      ))}

      {open && <ExpertLog agentId={agentId} expert={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function ExpertLog({ agentId, expert, onClose }: { agentId: string; expert: ExpertRun; onClose: () => void }) {
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
  }, [agentId, expert.id, expert.status, c.expertsLoadFailed])

  return (
    <Dialog onOpenChange={next => { if (!next) {onClose()} }} open>
      <DialogContent className="flex max-h-[80vh] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle className="truncate text-sm">{c.expertsLog}</DialogTitle>
          <p className="truncate text-[0.72rem] text-(--ui-text-tertiary)">{goalLine(expert)}</p>
        </DialogHeader>

        <div className="flex min-h-[200px] flex-1 flex-col gap-1 overflow-y-auto py-1">
          {messages === null && !error && (
            <span className="text-[0.75rem] text-(--ui-text-tertiary)">{c.expertsLoading}</span>
          )}
          {error && <span className="text-[0.72rem] text-(--ui-red)">{error}</span>}
          {messages?.map((message, index) => {
            const line = logLine(message)

            if (!line.text) {
              return null
            }

            return (
              <div
                className={cn(
                  'whitespace-pre-wrap rounded-md px-2 py-1 text-[0.74rem] leading-snug',
                  line.kind === 'tool'
                    ? 'font-mono text-[0.68rem] text-(--ui-text-quaternary)'
                    : message.role === 'user'
                      ? 'self-end bg-(--ui-accent) text-(--ui-accent-foreground)'
                      : 'self-start bg-(--ui-bg-secondary) text-(--ui-text)'
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
