import { DetailPeek } from '@/app/shell/rail-kit'
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
 *
 * **两个浮层共用一个壳**（WP-DOSSIER-RAIL-UNIFY，用户 11-14 09:4x
 * 「以后在这个地方添加的东西除了子内容的差异性，UI/UX共用的是同一套组件」）：
 * 目标详情与阶段详情都走 `DetailPeek` —— 以前是同一份手搓 `CARD` 常量用了两处，
 * 外观一样、代码两份；这刀起，rail 上再有一处「点开看详情」也走它。
 */

const NODE = 'size-2 rounded-full border transition-colors'
const NODE_DONE = 'border-emerald-500 bg-emerald-500'
const NODE_TODO = 'border-(--ui-stroke-tertiary) bg-(--ui-bg-primary)'

export function DossierStageBar({
  goal,
  stages
}: {
  goal: string
  stages: SurfaceStage[]
}) {
  const { t } = useI18n()
  const v = t.surface

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
      <DetailPeek
        className="absolute right-0 top-0 z-10"
        content={
          <>
            <span className="mb-0.5 block font-medium text-foreground">{v.dossierGoalLabel}</span>
            <span className="block text-(--ui-text-secondary)">{goal || v.dossierGoalEmpty}</span>
          </>
        }
        placement="top-end"
        testId="dossier-goal-detail"
      >
        <button
          aria-label={v.dossierGoalLabel}
          className="grid size-6 place-items-center rounded-md text-(--ui-text-tertiary) transition-colors hover:bg-(--ui-control-hover-background) hover:text-foreground"
          data-testid="dossier-goal"
          type="button"
        >
          <Codicon name="target" size="0.9rem" />
        </button>
      </DetailPeek>

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
            <DetailPeek
              className="relative z-10 w-full justify-center"
              content={
                <>
                  <span className="block font-medium text-foreground">{stage.title}</span>
                  {stage.detail && (
                    <span className="mt-0.5 block text-(--ui-text-secondary)">{stage.detail}</span>
                  )}
                  {/* 大概什么时候（WP-FIVE-DOSSIERS）：那是**数据**，不是文案 ⇒ 不新增
                      i18n 键。节点没写时间就什么都不显示。 */}
                  {stage.due && (
                    <span className="mt-0.5 block text-(--ui-text-tertiary)">{stage.due}</span>
                  )}
                </>
              }
              placement="top-center"
              testId={`dossier-stage-detail-${stage.id}`}
            >
              <button
                aria-label={stage.title}
                className="grid size-4 place-items-center"
                data-testid={`dossier-stage-${stage.id}`}
                type="button"
              >
                <span className={cn(NODE, stage.done ? NODE_DONE : NODE_TODO)} />
              </button>
            </DetailPeek>
            <span className="max-w-full truncate text-[0.58rem] leading-tight text-(--ui-text-tertiary)">
              {stage.title}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
