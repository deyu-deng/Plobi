import { useState } from 'react'

import type { SurfaceStage } from '@/app/surface/types'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

/**
 * 阶段进度条 ＋ 右上角那枚**目标**图标（WP-DOSSIER-SHAPE）。
 *
 * 用户 11-09 定下的卡面五段里的两段：
 *
 * * ③ **阶段进度条**：一个节点 ＝ 一个阶段（`dossier.json` 里的 `stages[]`）；
 *   hover 那个节点 ⇒ 出这一阶段的详情（标题 ＋ 交付标准）。
 * * ② **目标**：进度条**右上角**一枚矢量图标，hover 出目标详情。
 *
 * 为什么目标不单独占一段：它是**那个阶段的去处**，做成一枚图标比再开一个抽屉省事，
 * 也比一整段文字省地方（用户原话：「Plan现在占的位置太长了」）。
 */

const NODE = 'size-2 rounded-full border transition-colors'
const NODE_DONE = 'border-emerald-500 bg-emerald-500'
const NODE_TODO = 'border-(--ui-stroke-tertiary) bg-(--ui-bg-primary)'

const CARD =
  'pointer-events-none absolute z-20 w-44 rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) px-2 py-1.5 text-left text-[0.65rem] leading-relaxed shadow-lg'

export function DossierStageBar({
  goal,
  stages
}: {
  goal: string
  stages: SurfaceStage[]
}) {
  const { t } = useI18n()
  const v = t.surface
  const [hoverStage, setHoverStage] = useState<string | null>(null)
  const [goalOpen, setGoalOpen] = useState(false)

  if (stages.length === 0) {
    return (
      <p className="text-[0.68rem] leading-relaxed text-(--ui-text-tertiary)" data-testid="dossier-stages-empty">
        {v.dossierStagesEmpty}
      </p>
    )
  }

  return (
    <div className="relative" data-testid="dossier-stage-bar">
      {/* 目标那枚图标：进度条右上角。hover ⇒ 出目标详情。 */}
      <button
        aria-label={v.dossierGoalLabel}
        className="absolute right-0 top-0 z-10 grid size-6 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground"
        data-testid="dossier-goal"
        onBlur={() => setGoalOpen(false)}
        onFocus={() => setGoalOpen(true)}
        onMouseEnter={() => setGoalOpen(true)}
        onMouseLeave={() => setGoalOpen(false)}
        type="button"
      >
        <Codicon name="target" size="0.9rem" />
      </button>

      {goalOpen && (
        <div className={cn(CARD, 'bottom-full right-0 mb-1')} data-testid="dossier-goal-detail">
          <span className="mb-0.5 block font-medium text-foreground">{v.dossierGoalLabel}</span>
          <span className="block text-(--ui-text-secondary)">{goal || v.dossierGoalEmpty}</span>
        </div>
      )}

      <ol aria-label={v.dossierStagesSection} className="flex items-start pr-6">
        {stages.map((stage, index) => (
          <li className="relative flex min-w-0 flex-1 flex-col items-center gap-1" key={stage.id}>
            {index > 0 && (
              <span
                aria-hidden
                className={cn(
                  'absolute top-1 right-1/2 h-px w-full',
                  stage.done ? 'bg-emerald-500/60' : 'bg-(--ui-bg-quinary)'
                )}
              />
            )}
            <button
              aria-label={stage.title}
              className="relative z-10 grid size-4 place-items-center"
              data-testid={`dossier-stage-${stage.id}`}
              onBlur={() => setHoverStage(null)}
              onFocus={() => setHoverStage(stage.id)}
              onMouseEnter={() => setHoverStage(stage.id)}
              onMouseLeave={() => setHoverStage(null)}
              type="button"
            >
              <span className={cn(NODE, stage.done ? NODE_DONE : NODE_TODO)} />
            </button>
            <span className="max-w-full truncate text-[0.58rem] leading-tight text-(--ui-text-tertiary)">
              {stage.title}
            </span>

            {hoverStage === stage.id && (
              <span className={cn(CARD, 'bottom-full left-1/2 mb-1 -translate-x-1/2')} data-testid={`dossier-stage-detail-${stage.id}`}>
                <span className="block font-medium text-foreground">{stage.title}</span>
                {stage.detail && (
                  <span className="mt-0.5 block text-(--ui-text-secondary)">{stage.detail}</span>
                )}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
