import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { AgentNotesList } from '@/app/chat/right-rail/notes-list'
import { fetchVaultAgent } from '@/app/surface/api'
import { CompactMarkdown } from '@/components/chat/compact-markdown'
import { Badge } from '@/components/ui/badge'
import { Codicon } from '@/components/ui/codicon'
import { ErrorCard } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { setDossierPanelMode } from '@/store/layout'

import { fetchDossierTasks, setDossierTaskDone } from './dossier-api'

/**
 * 档案卡：**对话卡右侧那条长卡**（用户 11-08 21:1x）。
 *
 * 形态照 Qoder 的「任务监控」（逆向证据见 `Docs/tasks/dossier-monitor-card-task.md` §0）：
 * Qoder 那块面板有两种显示模式——Floating（浮在屏上的小窗）与 Fixed（钉在对话右侧的一条长卡），
 * 而用户要的是 **Fixed**。之前那枚图标弹出来的小弹窗就是 Floating 形态，所以「不像」。
 *
 * 卡里是**三个纵向部分**，不是三个抽屉：
 *   1. 计划：这个 Agent 的总体计划 ＋ 里程碑进度
 *   2. 进度：按日期切换的每日清单（人和 Agent 都能标完成）
 *   3. 相关笔记：点一条 ⇒ 在**右栏**开一个页签（与点开一个文件同一动作）
 */

function stripFrontmatter(text: string): string {
  if (!text.startsWith('---')) {
    return text
  }

  const end = text.indexOf('\n---', 3)

  return end < 0 ? text : text.slice(end + 4).replace(/^\s*\n/, '')
}

const SECTION_HEAD =
  'flex items-center gap-1.5 px-3 pt-3 pb-1 text-[0.68rem] font-medium uppercase tracking-wide text-(--ui-text-tertiary)'

const SECTION_BODY = 'px-3 pb-3'

const DAY_CHIP =
  'shrink-0 rounded-full border border-(--ui-stroke-tertiary) px-2 py-0.5 text-[0.65rem] text-(--ui-text-secondary) transition-colors hover:bg-(--ui-control-hover-background)'

const DAY_CHIP_ACTIVE =
  'shrink-0 rounded-full border border-transparent bg-(--ui-control-hover-background) px-2 py-0.5 text-[0.65rem] font-medium text-foreground'

const TASK_ROW = 'flex w-full items-start gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-(--ui-control-hover-background)/60'

function isToday(date: string): boolean {
  const now = new Date()

  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
    now.getDate()
  ).padStart(2, '0')}`

  return date === today
}

export function DossierPanel({ agentId, className }: { agentId: string; className?: string }) {
  const { t } = useI18n()
  const v = t.surface
  const queryClient = useQueryClient()
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  // **钩子必须在任何提前 return 之前调用**——agentId 一变就换钩子数 ⇒ React 崩。
  const page = useQuery({
    enabled: Boolean(agentId),
    queryFn: () => fetchVaultAgent(agentId),
    queryKey: ['surface-agent', agentId],
    staleTime: 5 * 60 * 1000
  })

  const tasks = useQuery({
    enabled: Boolean(agentId),
    queryFn: () => fetchDossierTasks(agentId),
    queryKey: ['dossier-tasks', agentId],
    staleTime: 30 * 1000
  })

  const toggle = useMutation({
    mutationFn: ({ date, done, id }: { date: string; done: boolean; id: string }) =>
      setDossierTaskDone(agentId, date, id, done),
    onSuccess: data => {
      queryClient.setQueryData(['dossier-tasks', agentId], data)
    }
  })

  // 日期倒序（新的在前）；没选就落在第一天。
  const days = useMemo(() => [...(tasks.data?.days ?? [])].sort((a, b) => (a.date < b.date ? 1 : -1)), [tasks.data])
  const activeDate = selectedDate && days.some(d => d.date === selectedDate) ? selectedDate : days[0]?.date
  const activeDay = days.find(d => d.date === activeDate)

  if (!agentId) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 px-4 py-10 text-center', className)}>
        <Codicon className="text-(--ui-text-tertiary)" name="notebook" size="1.5rem" />
        <p className="text-[0.7rem] font-medium text-foreground">{v.noAgentSelected}</p>
      </div>
    )
  }

  if (page.isError) {
    const detail = page.error instanceof Error ? page.error.message : String(page.error)

    return (
      <div className={cn('p-4', className)}>
        <ErrorCard
          action={{ label: t.common.retry, onClick: () => void page.refetch() }}
          detail={detail}
          title={v.agentLoadFailed}
        />
      </div>
    )
  }

  if (!page.data) {
    return (
      <div className={cn('flex h-32 items-center justify-center', className)}>
        <Codicon className="animate-spin text-(--ui-text-tertiary)" name="loading" size="1rem" />
      </div>
    )
  }

  const agent = page.data
  const plan = agent.docs.find(doc => doc.key === 'plan')
  const milestones = agent.milestones
  const pct = milestones.total > 0 ? (milestones.done / milestones.total) * 100 : 0

  return (
    <aside
      className={cn('flex min-h-0 min-w-0 flex-col overflow-y-auto border-l border-(--ui-stroke-tertiary)', className)}
      data-testid="dossier-panel"
    >
      {/* 抬头：名字 ＋ 状态 ＋ 收起 */}
      <header className="flex items-start gap-2 px-3 pt-3 pb-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="truncate text-[0.9rem] font-semibold leading-tight">{agent.displayName}</h3>
            {agent.status && (
              <Badge variant={agent.status === 'active' ? 'default' : 'warn'}>
                {v.statuses[agent.status] ?? agent.status}
              </Badge>
            )}
          </div>
          {agent.description && (
            <p className="mt-1 line-clamp-2 text-[0.66rem] leading-relaxed text-(--ui-text-tertiary)">
              {agent.description}
            </p>
          )}
        </div>
        <button
          aria-label={v.dossierCollapse}
          className="grid size-7 shrink-0 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground"
          onClick={() => setDossierPanelMode('closed')}
          title={v.dossierCollapse}
          type="button"
        >
          <Codicon name="close" size="0.8rem" />
        </button>
      </header>

      {/* 一、计划 */}
      <section className="border-t border-(--ui-stroke-tertiary)">
        <h4 className={SECTION_HEAD}>{v.dossierPlanSection}</h4>
        <div className={SECTION_BODY}>
          {milestones.total > 0 && (
            <div className="mb-2 space-y-1">
              <div className="flex items-center justify-between text-[0.62rem] text-(--ui-text-tertiary)/70">
                <span>{v.milestones(milestones.done, milestones.total)}</span>
                <span className="tabular-nums">{Math.round(pct)}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-(--ui-bg-quinary)">
                <div className="h-full rounded-full bg-emerald-500/70" style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}
          {plan?.exists ? (
            <CompactMarkdown
              className="text-[0.72rem] leading-relaxed text-(--ui-text-secondary)"
              text={stripFrontmatter(plan.text)}
            />
          ) : (
            <p className="text-[0.68rem] text-(--ui-text-tertiary)">{v.dossierPlanEmpty}</p>
          )}
        </div>
      </section>

      {/* 二、进度（按日清单） */}
      <section className="border-t border-(--ui-stroke-tertiary)">
        <h4 className={SECTION_HEAD}>{v.dossierProgressSection}</h4>
        <div className={SECTION_BODY}>
          {tasks.isError ? (
            <ErrorCard
              action={{ label: t.common.retry, onClick: () => void tasks.refetch() }}
              detail={tasks.error instanceof Error ? tasks.error.message : String(tasks.error)}
              title={v.dossierTasksLoadFailed}
            />
          ) : tasks.data?.error ? (
            // **文件在，但读不出**——这是「读不到」，不是「一个都没有」。
            // 空表会骗人：看上去像今天没活干，其实是文件坏了。
            <ErrorCard
              action={{ label: t.common.retry, onClick: () => void tasks.refetch() }}
              detail={tasks.data.error}
              title={v.dossierTasksLoadFailed}
            />
          ) : days.length === 0 ? (
            <p className="text-[0.68rem] leading-relaxed text-(--ui-text-tertiary)">{v.dossierTasksEmptyDesc}</p>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap gap-1" data-testid="dossier-days">
                {days.map(day => {
                  const active = day.date === activeDate

                  return (
                    <button
                      aria-pressed={active}
                      className={active ? DAY_CHIP_ACTIVE : DAY_CHIP}
                      data-testid={`dossier-day-${day.date}`}
                      key={day.date}
                      onClick={() => setSelectedDate(day.date)}
                      type="button"
                    >
                      {isToday(day.date) ? v.dossierToday : day.date.slice(5)}
                    </button>
                  )
                })}
              </div>
              <ul className="flex flex-col gap-px" data-testid="dossier-tasks">
                {(activeDay?.items ?? []).map(item => (
                  <li key={item.id}>
                    <button
                      aria-pressed={item.done}
                      className={TASK_ROW}
                      data-testid={`dossier-task-${item.id}`}
                      disabled={toggle.isPending}
                      onClick={() =>
                        toggle.mutate({ date: activeDate ?? '', done: !item.done, id: item.id })
                      }
                      type="button"
                    >
                      <Codicon
                        className={cn(
                          'mt-0.5 shrink-0',
                          item.done ? 'text-emerald-500' : 'text-(--ui-text-tertiary)/70'
                        )}
                        name={item.done ? 'pass' : 'circle-outline'}
                        size="0.9rem"
                      />
                      <span
                        className={cn(
                          'min-w-0 flex-1 text-[0.72rem] leading-relaxed',
                          item.done
                            ? 'text-(--ui-text-tertiary) line-through'
                            : 'text-(--ui-text-secondary)'
                        )}
                      >
                        {item.title}
                      </span>
                      {item.done && item.doneBy && (
                        <span className="shrink-0 text-[0.6rem] text-(--ui-text-tertiary)/70">
                          {item.doneBy === 'user' ? v.dossierDoneByUser : v.dossierDoneByAgent}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </section>

      {/* 三、相关笔记（点一条 ⇒ 右栏开一个页签） */}
      <section className="border-t border-(--ui-stroke-tertiary)">
        <h4 className={SECTION_HEAD}>{v.dossierNotesSection}</h4>
        <div className="px-2 pb-3">
          <AgentNotesList agentId={agentId} />
        </div>
      </section>
    </aside>
  )
}
