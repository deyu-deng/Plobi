import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { ExpertRoleSpec } from './expert-roles'
import { elapsedText, getAgentExperts } from './expert-runs'
import type { ExpertRun, ExpertRunsData } from './expert-runs'

/**
 * L2 派出去的临时专家（WP-L3-EXPERTS，用户 10-28 口径）。
 *
 * 11-01 起它不再是「一整格面板 + 点开弹窗」，而是「专家」那枚抽屉里的**角色名册**：
 * 四类角色（文件 / 浏览器 / 软件 / 电脑）各占一行，**这里不放会话流水**
 * （用户 11-01：「专家下面不要放会话了。应该放五个专家对应的图标和名称」——后端是四类）。
 *
 * 点一行就打开这个角色最近那条会话：用户选定的形态是「一个专家一张嘴」。
 * 名单与日志都读同一个状态库——专家就是一条 `source=subagent` 的子会话。
 */
/** 名单自读间隔：正在干的那一行会自己冒出状态点、自己收尾。 */
const REFRESH_MS = 3500

export function useAgentExperts(agentId: string): ExpertRun[] {
  const [data, setData] = useState<null | ExpertRunsData>(null)

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
    const timer = setInterval(() => load(false), REFRESH_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId])

  return data?.experts ?? []
}

/**
 * 一行专家角色：矢量图标 + 名词（+ 跑过几次 / 正在干的状态点）。
 *
 * 和一级抽屉行同一套行高与内距，只是缩进在抽屉里；点它是「打开这张嘴」而不是展开，
 * 所以没有折角——折角是「还有下一层」的承诺，这里没有下一层。
 *
 * **永远可点**（11-04 用户：「这几个专家为什么不能点击…根本点击不了」）：
 * 没派过活时也打开一张卡，写清这个角色是什么、能干什么、怎么让它出场。
 * 灰着但点不动是最糟的一种——用户以为是坏了。
 */
export function ExpertRoleRow({
  onOpen,
  role,
  runs
}: {
  onOpen: () => void
  role: ExpertRoleSpec
  runs: ExpertRun[]
}) {
  const { t } = useI18n()
  const running = runs.find(run => run.status === 'running')
  const idle = runs.length === 0

  return (
    <button
      className={cn(
        'flex h-7 w-full shrink-0 items-center gap-2 rounded-md px-2 text-left text-[0.8125rem] font-medium transition-colors duration-100 ease-out',
        idle
          ? 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-secondary) hover:transition-none'
          : 'text-(--ui-text-secondary) hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none'
      )}
      onClick={onOpen}
      title={running ? `${t.console[role.labelKey]} · ${elapsedText(running)}` : t.console[role.labelKey]}
      type="button"
    >
      <span className="grid size-4 shrink-0 place-items-center">
        <Codicon name={role.icon} size="0.875rem" />
      </span>
      <span className="min-w-0 flex-1 truncate">{t.console[role.labelKey]}</span>
      {runs.length > 1 ? (
        <span className="shrink-0 text-[0.68rem] font-medium text-(--ui-text-quaternary)">{runs.length}</span>
      ) : null}
      {running ? (
        <span
          aria-label={t.console.expertsRunning}
          className="size-1.5 shrink-0 animate-pulse rounded-full bg-(--ui-accent)"
        />
      ) : null}
    </button>
  )
}
