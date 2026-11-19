import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { AgentNotesList } from '@/app/chat/right-rail/notes-list'
import { PanelEmpty } from '@/app/overlays/panel'
import { MonitorEntryButton } from '@/app/shell/monitor-entry-button'
import { useMonitorFace } from '@/app/shell/monitor-faces'
import { MonitorEmpty, MonitorIcon, MonitorRow, MonitorSection } from '@/app/shell/monitor-panel'
import { fetchVaultAgent } from '@/app/surface/api'
import { Codicon } from '@/components/ui/codicon'
import { ErrorCard } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $monitorCollapsedSections, toggleMonitorSection } from '@/store/layout'

import { fetchDossierTasks, setDossierTaskDone } from './dossier-api'
import { DossierStageBar } from './dossier-stage-bar'

/**
 * 档案那一面的**内容**（用户 11-08 21:1x 起，22:3x 按 Qoder 实物截图改形；
 * 11-19 16:0x 换成统一容器，17:0x 收进**那一张卡**）。
 *
 * **11-19 17:0x 这一刀：档案不再是三样东西里的一样，而是那一张卡的一面。**
 * 这一档因此**只写内容**（四段 ＋ 各自的行）：壳（卡、头、图钉、拖动、显隐）
 * 全在 `app/shell/monitor-region.tsx`，入口在 `monitor-entry-button.tsx`。
 *
 * **卡里四段**（用户 11-09 定的，自上而下）：
 *   ① 项目当前状况：一段话（与「记忆系统喂给 L1 的各 L2 现状摘要」**同一份数据**）
 *   ② 目标：进度条右上角一枚矢量图标，hover 出目标详情
 *   ③ 阶段进度条：一个节点 ＝ 一个阶段，hover 出该阶段详情
 *   ④ 进度视窗：按日期切换的每日清单（人和 Agent 都能标完成）
 *   ⑤ 相关笔记：点一条 ⇒ 在**右栏**开一个页签（与点开一个文件同一动作）
 *
 * ①③④⑤ 都来自**档案本体**（``dossier.json``）与 ``tasks.json``；md 那一对已退役。
 *
 * **换壳之前**这一档是**手搓全家桶**（自搓外层壳／头／分区容器／行／空态，字号七种、
 * 分区标题挂着**常驻的折角**）。换之后：外层 `MonitorPanel`、三段 `MonitorSection`、
 * 段内条目 `MonitorRow`、图标 `MonitorIcon`、空态 `MonitorEmpty` —— **同一把尺**
 * （280px 列、28px 行、24px 方形图标托、12px 字、三级字色、6/16px 间距、零描边、
 * hover 才出底色）。
 *
 * **不再有常驻折角**：分区标题就是一行 12px 字，折叠状态用 `MonitorSection` 自带的
 * 那颗状态点表达（点标题才切换；折叠态仍存在 store 里 —— `$monitorCollapsedSections`
 * ／`toggleMonitorSection`，没换存储）。
 *
 * **屏上文案一个字没动**：沿用现有 `v.dossier*` 那些键（i18n 五档别的线在飞，这一刀
 * 不新增键）。
 */

function isToday(date: string): boolean {
  const now = new Date()

  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
    now.getDate()
  ).padStart(2, '0')}`

  return date === today
}

export function DossierFace({ agentId }: { agentId: string }) {
  const { t } = useI18n()
  const v = t.surface
  const queryClient = useQueryClient()
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const collapsedSections = useStore($monitorCollapsedSections)

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
  const activeItems = activeDay?.items ?? []

  if (!agentId) {
    return (
      <div className="flex flex-col items-center justify-center">
        <PanelEmpty className="w-full" description={v.noAgentSelected} icon="notebook" size="compact" />
      </div>
    )
  }

  if (page.isError) {
    const detail = page.error instanceof Error ? page.error.message : String(page.error)

    return (
      <div className="p-4">
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
      <div className="flex h-32 items-center justify-center">
        <Codicon className="animate-spin text-(--ui-text-tertiary)" name="loading" size="1rem" />
      </div>
    )
  }

  const agent = page.data
  const stages = agent.stages ?? []

  return (
    <>
      {/* ① 项目当前状况：一段话（与喂给 L1 的那份摘要是同一份数据） */}
      <div className="rounded-lg bg-(--ui-control-hover-background)/45 px-2.5 py-2">
        <div className="flex items-center gap-1.5 text-xs text-(--ui-text-tertiary)">
          <MonitorIcon>
            <Codicon className="text-emerald-600/80" name="git-branch" />
          </MonitorIcon>
          {/* 谁的档案：卡头那一行现在写的是「档案」这一面本身（统一件的），名字落在这里。 */}
          <span className="truncate text-(--ui-text-secondary)">{agent.displayName}</span>
          <span>·</span>
          <span>{agent.updated ? v.dossierUpdatedAt(agent.updated) : v.dossierUpdatedAtUnknown}</span>
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-(--ui-text-secondary)" data-testid="dossier-summary">
          {/* 坏文件与「还没写」是两件事：读不到就说读不到，不许糊成「什么都没有」。 */}
          {agent.profileError ? v.dossierProfileLoadFailed : agent.summary || v.dossierStatusEmpty}
        </p>
      </div>

      {/* ②③ 目标（进度条右上角那枚图标）＋ 阶段进度条 */}
      <MonitorSection
        collapsed={collapsedSections.stages === true}
        count={stages.length}
        onToggle={() => toggleMonitorSection('stages')}
        title={v.dossierStagesSection}
      >
        <DossierStageBar goal={agent.goal} stages={stages} />
      </MonitorSection>

      {/* ④ 进度（按日清单） */}
      <MonitorSection
        collapsed={collapsedSections.progress === true}
        count={activeItems.length}
        onToggle={() => toggleMonitorSection('progress')}
        title={v.dossierProgressSection}
      >
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
          <p className="text-xs leading-relaxed text-(--ui-text-tertiary)">{v.dossierTasksEmptyDesc}</p>
        ) : (
          <>
            {/* 按日切换：一排小按钮（零描边，hover 才出底色）。 */}
            <div className="flex flex-wrap gap-1.5" data-testid="dossier-days">
              {days.map(day => {
                const active = day.date === activeDate

                return (
                  <button
                    aria-pressed={active}
                    className={cn(
                      'shrink-0 rounded-md px-1.5 py-0.5 text-xs transition-colors hover:bg-(--ui-row-hover-background)',
                      active ? 'bg-(--ui-bg-secondary) font-medium text-foreground' : 'text-(--ui-text-secondary)'
                    )}
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

            {activeItems.length === 0 ? (
              /* 空的那块就空着（屏上纪律：不写「暂无…」这类安慰话）。 */
              <MonitorEmpty />
            ) : (
              <ul className="flex flex-col gap-1.5" data-testid="dossier-tasks">
                {activeItems.map(item => (
                  <li key={item.id}>
                    <MonitorRow
                      ariaPressed={item.done}
                      disabled={toggle.isPending}
                      icon={
                        <Codicon
                          className={cn(item.done ? 'text-emerald-500' : 'text-(--ui-text-tertiary)')}
                          name={item.done ? 'pass' : 'circle-outline'}
                        />
                      }
                      label={
                        <span
                          className={cn(item.done && 'text-(--ui-text-tertiary) line-through')}
                          data-testid={`dossier-task-${item.id}`}
                        >
                          {item.title}
                        </span>
                      }
                      onClick={() =>
                        toggle.mutate({ date: activeDate ?? '', done: !item.done, id: item.id })
                      }
                      value={
                        item.done && item.doneBy
                          ? item.doneBy === 'user'
                            ? v.dossierDoneByUser
                            : v.dossierDoneByAgent
                          : undefined
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </MonitorSection>

      {/* ⑤ 相关笔记（点一条 ⇒ 右栏开一个页签） */}
      <MonitorSection
        collapsed={collapsedSections.notes === true}
        onToggle={() => toggleMonitorSection('notes')}
        title={v.dossierNotesSection}
      >
        <AgentNotesList agentId={agentId} variant="item" />
      </MonitorSection>
    </>
  )
}

/**
 * 抬头右上角那一族里的「档案」**入口**。
 *
 * 形状与行为都在统一件 `monitor-entry-button.tsx` 里（跟待放行、写脑待批**同一个组件**）。
 */
export function DossierButton() {
  const face = useMonitorFace('dossier')

  return <MonitorEntryButton face={face} />
}
