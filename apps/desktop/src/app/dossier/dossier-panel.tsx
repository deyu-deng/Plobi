import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { AgentNotesList } from '@/app/chat/right-rail/notes-list'
import { fetchVaultAgent } from '@/app/surface/api'
import { Codicon } from '@/components/ui/codicon'
import { ErrorCard } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $dossierPanelPinned, toggleDossierPanelPin } from '@/store/layout'

import { fetchDossierTasks, setDossierTaskDone } from './dossier-api'
import { DossierStageBar } from './dossier-stage-bar'

/**
 * 档案卡（用户 11-08 21:1x 起，22:3x 按 Qoder 实物截图改形；11-09 按「档案从 md 退役」
 * 改成 v2）。
 *
 * **形态**：Qoder 的「任务监控」是一张**浮在对话右上角的圆角卡片**（Floating），
 * 抬头右上角一枚**图钉**把它钉成对话右侧的一列（Fixed）。用户 11-08 22:3x 看了实物
 * 截图后定的：默认**浮层**（对话不被挤窄），图钉切「固定／取消固定」。
 * 之前那版是**停靠列**（对话卡改 `flex-row`）——那是照 asar 里的词条推的，
 * 实物截图证明它默认是浮在对话上的那张卡。
 *
 * **卡里五段**（用户 11-09 定的，自上而下）：
 *   ① 项目当前状况：一段话（与「记忆系统喂给 L1 的各 L2 现状摘要」**同一份数据**）
 *   ② 目标：进度条右上角一枚矢量图标，hover 出目标详情
 *   ③ 阶段进度条：一个节点 ＝ 一个阶段，hover 出该阶段详情
 *   ④ 进度视窗：按日期切换的每日清单（人和 Agent 都能标完成）
 *   ⑤ 相关笔记：点一条 ⇒ 在**右栏**开一个页签（与点开一个文件同一动作）
 *
 * ①③④⑤ 都来自**档案本体**（``dossier.json``）与 ``tasks.json``；md 那一对已退役。
 */

const SECTION_TITLE = 'px-3 pt-3 pb-1 text-[0.78rem] font-medium text-foreground'

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
  const pinned = useStore($dossierPanelPinned)

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
  const stages = agent.stages ?? []

  return (
    <aside
      className={cn('flex min-h-0 flex-col overflow-hidden bg-(--ui-bg-primary)', className)}
      data-testid="dossier-panel"
    >
      {/* 抬头：标题 ＋ 那一格是谁 ＋ 图钉（固定／取消固定） */}
      <header className="flex items-center gap-2 px-3 py-2.5">
        <h3 className="shrink-0 text-[0.8rem] font-semibold text-foreground">{v.agentDossier}</h3>
        <span className="min-w-0 flex-1 truncate text-[0.7rem] text-(--ui-text-tertiary)">
          {agent.displayName}
        </span>
        <button
          aria-label={pinned ? v.dossierUnpin : v.dossierPin}
          className={cn(
            'grid size-6 shrink-0 place-items-center rounded-md transition-colors hover:bg-(--ui-control-hover-background)',
            pinned ? 'text-foreground' : 'text-(--ui-text-tertiary)'
          )}
          data-testid="dossier-pin"
          onClick={() => toggleDossierPanelPin()}
          title={pinned ? v.dossierUnpin : v.dossierPin}
          type="button"
        >
          <Codicon name="pin" size="0.85rem" />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ① 项目当前状况：一段话（与喂给 L1 的那份摘要是同一份数据） */}
        <div className="mx-2.5 mt-1 rounded-lg bg-(--ui-control-hover-background)/45 px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[0.68rem] text-(--ui-text-tertiary)">
            <Codicon className="text-emerald-600/80" name="git-branch" size="0.85rem" />
            <span>{agent.updated ? v.dossierUpdatedAt(agent.updated) : v.dossierUpdatedAtUnknown}</span>
          </div>
          <p className="mt-1.5 text-[0.72rem] leading-relaxed text-(--ui-text-secondary)" data-testid="dossier-summary">
            {/* 坏文件与「还没写」是两件事：读不到就说读不到，不许糊成「什么都没有」。 */}
            {agent.profileError ? v.dossierProfileLoadFailed : agent.summary || v.dossierStatusEmpty}
          </p>
        </div>

        {/* ②③ 目标（进度条右上角那枚图标）＋ 阶段进度条 */}
        <section>
          <h4 className={SECTION_TITLE}>{v.dossierStagesSection}</h4>
          <div className={SECTION_BODY}>
            <DossierStageBar goal={agent.goal} stages={stages} />
          </div>
        </section>

        {/* ④ 进度（按日清单） */}
        <section>
          <h4 className={SECTION_TITLE}>{v.dossierProgressSection}</h4>
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

        {/* ⑤ 相关笔记（点一条 ⇒ 右栏开一个页签） */}
        <section>
          <h4 className={SECTION_TITLE}>{v.dossierNotesSection}</h4>
          <div className="px-2 pb-3">
            <AgentNotesList agentId={agentId} variant="item" />
          </div>
        </section>
      </div>
    </aside>
  )
}
