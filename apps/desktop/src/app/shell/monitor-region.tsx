import { useStore } from '@nanostores/react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'

import { DossierFace } from '@/app/dossier/dossier-panel'
import { MindPendingFace } from '@/app/mind-pending-overlay'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $monitorFace, $monitorPinned, toggleMonitorPin } from '@/store/layout'

import { useMonitorFaceMetas } from './monitor-faces'
import {
  MONITOR_MAX_HEIGHT,
  MonitorCardHeader,
  MonitorPanel,
  type MonitorPanelMode
} from './monitor-panel'
import { PendingTicketsFace } from './pending-tickets'

/**
 * **那一张卡**（用户 11-19 17:0x）。
 *
 * 档案／待放行／写脑待批**不再是三张卡**，是这一张：抬头右上角那三枚入口换的是
 * **这一张卡显示哪一面**，不是「开出三样东西」。
 *
 * - 形态还是那两种：**并排一列**（占对话右侧的位）／**浮层**（可拖、记位置）。
 * - 窄形态（对话卡装不下那一列）⇒ 整张卡折叠，入口展开**贴头浮层**。
 * - 头是**一份**（图标 ＋ 标题 ＋ 角标 ＋ 图钉）；内容按当前那一面换。
 *
 * **那一枚图钉切的是这张卡的形状**（并排／浮层），不是某一面的形状 ⇒ 三面都有它。
 */
export function MonitorRegion({
  agentId,
  className,
  headerDragProps,
  mode: modeProp,
  showPin = true,
  style
}: {
  /** 档案那一面跟的是哪个 Agent（其它两面不认它）。 */
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
   * 容器形态：`column` ＝ 占位兄弟列（本体无描边），`float` ＝ 可拖的浮层。
   *
   * **不传就按「有没有拖把手」推** —— 只有浮层才可拖（`headerDragProps` 的语义
   * 就是这个），固定列那一档控制器不传它。
   */
  mode?: MonitorPanelMode
  /**
   * 那枚**图钉**（切「并排一列／浮层」）还看不看。
   *
   * 窄形态下没有并排列可切（对话卡窄到装不下那一列，这张卡是折叠的）⇒ 那枚图钉
   * 在那里是个**点不动的按钮** ⇒ 藏起来（用户 11-16 19:4x 拍板的窄形态）。
   */
  showPin?: boolean
  /** 浮层形态拖动后用它落位（left/top）。 */
  style?: CSSProperties
}) {
  const { t } = useI18n()
  const v = t.surface
  const faces = useMonitorFaceMetas()
  const faceId = useStore($monitorFace)
  const pinned = useStore($monitorPinned)

  const face = faces.find(item => item.id === faceId) ?? faces[0]
  const mode: MonitorPanelMode = modeProp ?? (headerDragProps ? 'float' : 'column')

  /**
   * 浮层形态下的**高度上限**：统一容器给的是 560px（Qoder `max-h-[560px]`），
   * 这一层再收一道 —— 别高过对话卡（浮层是**贴在卡里**的，不是贴在窗口里）。
   * 写成 `min()` 是为了**两个上限同时在**：既不超 560，也不超卡片。
   */
  const floatStyle: CSSProperties | undefined =
    mode === 'float' ? { ...style, maxHeight: `min(${MONITOR_MAX_HEIGHT}px, calc(100% - 3.25rem))` } : style

  const header = (
    <MonitorCardHeader
      count={face?.count}
      dragProps={headerDragProps}
      icon={<Codicon className="size-3" name={face?.icon ?? 'book'} />}
      title={face?.title ?? ''}
      trailing={
        showPin ? (
          <Button
            aria-label={pinned ? v.dossierUnpin : v.dossierPin}
            aria-pressed={pinned}
            className={cn('shrink-0', pinned ? 'text-foreground' : 'text-(--ui-text-tertiary)')}
            data-testid="dossier-pin"
            onClick={() => toggleMonitorPin()}
            size="icon-xs"
            title={pinned ? v.dossierUnpin : v.dossierPin}
            variant="ghost"
          >
            <Codicon name="pin" size="0.85rem" />
          </Button>
        ) : null
      }
    />
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
        mode={mode}
        style={floatStyle}
        // 拖动定位（`app/shell/floating-position.ts`）按这枚 testid 找浮层**本体**
        // 来算拖动起点（那个元素的父级就是定位容器）⇒ 不能丢。
        // （testid 保持旧名：那一档是别线在飞的文件，本刀不去碰它。）
        testId="dossier-panel"
      >
        {face?.id === 'pending' ? (
          <PendingTicketsFace />
        ) : face?.id === 'mind-pending' ? (
          <MindPendingFace />
        ) : (
          <DossierFace agentId={agentId} />
        )}
      </MonitorPanel>
    </div>
  )
}
