import { useStore } from '@nanostores/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type CSSProperties, type PointerEvent as ReactPointerEvent, useMemo, useState } from 'react'

import { AgentNotesList } from '@/app/chat/right-rail/notes-list'
import { PanelEmpty } from '@/app/overlays/panel'
import {
  MONITOR_MAX_HEIGHT,
  MonitorDragHandle,
  MonitorEmpty,
  MonitorHeaderTitle,
  MonitorIcon,
  MonitorPanel,
  type MonitorPanelMode,
  MonitorRow,
  MonitorSection
} from '@/app/shell/monitor-panel'
import { fetchVaultAgent } from '@/app/surface/api'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ErrorCard } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import {
  $dossierCollapsedSections,
  $dossierPanelPinned,
  toggleDossierPanelPin,
  toggleDossierSection
} from '@/store/layout'

import { fetchDossierTasks, setDossierTaskDone } from './dossier-api'
import { DossierStageBar } from './dossier-stage-bar'

/**
 * 档案卡（用户 11-08 21:1x 起，22:3x 按 Qoder 实物截图改形；11-09 按「档案从 md 退役」
 * 改成 v2；**11-19 16:0x 换成统一容器**）。
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
 *
 * **11-19 16:0x 这一刀：壳与零件全部换成统一组件**（`app/shell/monitor-panel.tsx`）。
 * 用户原话：「我的重做 UI 不止是档案。是把对话卡右上角的东西的容器都统一用一个组件。
 * 就是我说的 Qoder 任务监控那样的。现在三个按钮一人一个样。关键是都很丑。」
 *
 * 换之前这一档是**手搓全家桶**（自搓外层壳／头／分区容器／行／空态，字号七种、
 * 分区标题挂着**常驻的折角**）。换之后：外层 `MonitorPanel`、三段 `MonitorSection`、
 * 段内条目 `MonitorRow`、图标 `MonitorIcon`、空态 `MonitorEmpty` —— **同一把尺**
 * （280px 列、28px 行、24px 方形图标托、12px 字、三级字色、6/16px 间距、零描边、
 * hover 才出底色）。
 *
 * **不再有常驻折角**：分区标题就是一行 12px 字，折叠状态用 `MonitorSection` 自带的
 * 那颗状态点表达（点标题才切换；折叠态仍存在 store 里 —— `$dossierCollapsedSections`
 * ／`toggleDossierSection`，没换存储）。
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

export function DossierPanel({
  agentId,
  className,
  headerDragProps,
  mode: modeProp,
  showPin = true,
  style
}: {
  agentId: string
  className?: string
  /**
   * 浮层形态下，头部是**拖把手**（Qoder 的浮层头部 `cursor-grab`，11-18 10:0x）。
   * 固定列形态不传（那一列不需要拖）。
   */
  headerDragProps?: {
    onPointerCancel?: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerDown?: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerMove?: (event: ReactPointerEvent<HTMLElement>) => void
    onPointerUp?: (event: ReactPointerEvent<HTMLElement>) => void
  }
  /**
   * 容器形态（11-19 统一容器那一刀）：`column` ＝ 占位兄弟列（本体无描边），
   * `float` ＝ 可拖的浮层（280px、圆角、一道淡边框、投影）。
   *
   * **不传就按「有没有拖把手」推** —— 只有浮层才可拖（`headerDragProps` 的语义
   * 就是这个），固定列那一档控制器不传它。显式传是给接线那一方留的口子。
   */
  mode?: MonitorPanelMode
  /**
   * 那枚**图钉**（切「并排一列／浮层」）还看不看。
   *
   * 窄形态下没有并排列可切（对话卡窄到装不下那一列，档案卡是折叠的）⇒ 那枚图钉
   * 在那里是个**点不动的按钮** ⇒ 藏起来（用户 11-16 19:4x 拍板的窄形态）。
   */
  showPin?: boolean
  /** 浮层形态拖动后用它落位（left/top）。 */
  style?: CSSProperties
}) {
  const { t } = useI18n()
  const v = t.surface
  const queryClient = useQueryClient()
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const pinned = useStore($dossierPanelPinned)
  const collapsedSections = useStore($dossierCollapsedSections)

  const mode: MonitorPanelMode = modeProp ?? (headerDragProps ? 'float' : 'column')

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

  /**
   * 浮层形态下的**高度上限**：统一容器给的是 560px（Qoder `max-h-[560px]`），
   * 这一层再收一道 —— 别高过对话卡（浮层是**贴在卡里**的，不是贴在窗口里）。
   * 写成 `min()` 是为了**两个上限同时在**：既不超 560，也不超卡片。
   */
  const floatStyle: CSSProperties | undefined =
    mode === 'float' ? { ...style, maxHeight: `min(${MONITOR_MAX_HEIGHT}px, calc(100% - 3.25rem))` } : style

  if (!agentId) {
    return (
      <div className={cn('flex flex-col items-center justify-center', className)}>
        <PanelEmpty className="w-full" description={v.noAgentSelected} icon="notebook" size="compact" />
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

  /** 抬头：标题 ＋ 那一格是谁 ＋ 图钉（固定／取消固定）。 */
  const title = <MonitorHeaderTitle>{v.agentDossier}</MonitorHeaderTitle>

  const subtitle = (
    <MonitorHeaderTitle className="min-w-0 flex-1 text-(--ui-text-tertiary)">{agent.displayName}</MonitorHeaderTitle>
  )

  const header = (
    <>
      {/* 拖把手只裹住「标题 ＋ 谁」——图钉那枚按钮自己要点得动，不该启动拖动。 */}
      {headerDragProps ? (
        <MonitorDragHandle className="min-w-0 flex-1" {...headerDragProps}>
          {title}
          {subtitle}
        </MonitorDragHandle>
      ) : (
        <>
          {title}
          {subtitle}
        </>
      )}
      {showPin ? (
        <Button
          aria-label={pinned ? v.dossierUnpin : v.dossierPin}
          aria-pressed={pinned}
          className={cn('shrink-0', pinned ? 'text-foreground' : 'text-(--ui-text-tertiary)')}
          data-testid="dossier-pin"
          onClick={() => toggleDossierPanelPin()}
          size="icon-xs"
          title={pinned ? v.dossierUnpin : v.dossierPin}
          variant="ghost"
        >
          <Codicon name="pin" size="0.85rem" />
        </Button>
      ) : null}
    </>
  )

  return (
    <div className={mode === 'float' ? 'absolute inset-0 pointer-events-none' : 'contents'}>
      <MonitorPanel
        className={cn(
          className,
          mode === 'column' && 'bg-(--ui-bg-primary)',
          mode === 'float' && 'pointer-events-auto'
        )}
        header={header}
        headerDragProps={headerDragProps}
        mode={mode}
        style={floatStyle}
        // 拖动定位（`app/shell/floating-position.ts`）按这枚 testid 找浮层**本体**
        // 来算拖动起点（那个元素的父级就是定位容器）⇒ 不能丢。
        testId="dossier-panel"
      >
        {/* ① 项目当前状况：一段话（与喂给 L1 的那份摘要是同一份数据） */}
        <div className="rounded-lg bg-(--ui-control-hover-background)/45 px-2.5 py-2">
          <div className="flex items-center gap-1.5 text-xs text-(--ui-text-tertiary)">
            <MonitorIcon>
              <Codicon className="text-emerald-600/80" name="git-branch" />
            </MonitorIcon>
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
          onToggle={() => toggleDossierSection('stages')}
          title={v.dossierStagesSection}
        >
          <DossierStageBar goal={agent.goal} stages={stages} />
        </MonitorSection>

        {/* ④ 进度（按日清单） */}
        <MonitorSection
          collapsed={collapsedSections.progress === true}
          count={activeItems.length}
          onToggle={() => toggleDossierSection('progress')}
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
          onToggle={() => toggleDossierSection('notes')}
          title={v.dossierNotesSection}
        >
          <AgentNotesList agentId={agentId} variant="item" />
        </MonitorSection>
      </MonitorPanel>
    </div>
  )
}
