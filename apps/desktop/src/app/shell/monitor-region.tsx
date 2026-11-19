import { useStore } from '@nanostores/react'
import { useLayoutEffect, useMemo, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'

import { DossierFace } from '@/app/dossier/dossier-panel'
import { MindPendingFace } from '@/app/mind-pending-overlay'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $monitorFace, $monitorPinned, toggleMonitorPin } from '@/store/layout'

import { clampFloatPosition } from './floating-position'
import { useMonitorFaceMetas } from './monitor-faces'
import {
  MONITOR_MARGIN,
  MONITOR_MAX_HEIGHT,
  MONITOR_WIDTH,
  MonitorCardHeader,
  MonitorPanel,
  type MonitorPanelMode,
  MonitorTopBarRow
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
 * - 头是**一份**（标题 ＋ 角标 ＋ 图钉）；内容按当前那一面换。
 *
 * **那一枚图钉切的是这张卡的形状**（并排／浮层），不是某一面的形状 ⇒ 三面都有它。
 *
 * 11-19 22:0x 收的三处：
 *
 * ① **浮层挂到 `document.body`**（portal ＋ `fixed` 壳）—— 原来它挂在对话卡里
 *    （`absolute`），祖先 `PaneMain`／`main` 的 `overflow-hidden` 会把它**裁掉**，
 *    它也压不过工作区卡 ⇒ 用户「拖到工作区卡片的时候，会藏到卡片底下导致看不到」。
 *    挂到 body 上就是**视口坐标**：裁不到、也在最上层。
 * ② **并排列不占顶栏**（`MonitorTopBarRow`）：这一列从顶栏下一行开始，并把顶栏
 *    那道线接过去 ⇒ 顶栏横跨整卡。
 * ③ **卡头没有矢量图标**（用户：「显示"档案"两个字就行了」）、头下也没有那条线。
 */

/**
 * 浮层**默认**落在哪儿：对话卡的右上角、**顶栏下一行之下**。
 *
 * 位置是**视口坐标**（浮层挂到 body 上）⇒ 从锚（对话卡那个元素）的 rect 算。
 */
function defaultFloatSpot(anchor: HTMLElement | null): { x: number; y: number } {
  if (!anchor) {
    return {
      x: Math.max(MONITOR_MARGIN, window.innerWidth - MONITOR_WIDTH - MONITOR_MARGIN),
      y: MONITOR_MARGIN
    }
  }

  const rect = anchor.getBoundingClientRect()
  // 顶栏那一行的下沿：抬头右边那几枚按钮住在那一行 ⇒ 浮层从**下一行**开始，不压它们。
  const topBar = anchor.querySelector('[data-chat-topbar]')
  const topBarRect = topBar?.getBoundingClientRect()
  const headerBottom = topBarRect ? topBarRect.bottom + 1 : rect.top

  return {
    x: Math.max(MONITOR_MARGIN, rect.right - MONITOR_WIDTH - MONITOR_MARGIN),
    y: headerBottom + 8
  }
}

export function MonitorRegion({
  agentId,
  anchorElement,
  className,
  floatPosition,
  headerDragProps,
  mode: modeProp,
  showPin = true
}: {
  /** 档案那一面跟的是哪个 Agent（其它两面不认它）。 */
  agentId: string
  /**
   * 浮层默认落点的**锚**：对话卡那个元素（控制器手里有那个 ref）。
   *
   * 浮层挂到 `document.body` ⇒ 位置是**视口坐标** ⇒ 默认落点从它的 rect 算。
   */
  anchorElement?: HTMLElement | null
  className?: string
  /** 拖出来的位置（**视口坐标**）。没拖过就＝null ⇒ 落在默认落点。 */
  floatPosition?: { x: number; y: number } | null
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
}) {
  const { t } = useI18n()
  const v = t.surface
  const faces = useMonitorFaceMetas()
  const faceId = useStore($monitorFace)
  const pinned = useStore($monitorPinned)

  const face = faces.find(item => item.id === faceId) ?? faces[0]
  const mode: MonitorPanelMode = modeProp ?? (headerDragProps ? 'float' : 'column')

  /** 浮层的默认落点（视口坐标）—— 跟着锚（对话卡）走，窗口一变就重算。 */
  const [defaultSpot, setDefaultSpot] = useState<{ x: number; y: number } | null>(null)

  useLayoutEffect(() => {
    if (mode !== 'float') {
      return
    }

    const measure = () => setDefaultSpot(defaultFloatSpot(anchorElement ?? null))

    measure()
    window.addEventListener('resize', measure)

    return () => window.removeEventListener('resize', measure)
  }, [anchorElement, mode])

  /**
   * 位置：拖过的用拖出来的，没拖过的用默认落点；**两者都夹回窗口内**
   * （`clampFloatPosition` 的口径本来就是窗口）。
   */
  const spot = useMemo(() => {
    const raw = floatPosition ?? defaultSpot

    if (!raw) {
      return null
    }

    return clampFloatPosition(raw, {
      height: MONITOR_MAX_HEIGHT,
      margin: MONITOR_MARGIN,
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
      width: MONITOR_WIDTH
    })
  }, [defaultSpot, floatPosition])

  /**
   * 浮层形态下的**高度上限**：统一容器给的是 560px（Qoder `max-h-[560px]`），
   * 这一层再收一道 —— 别高过窗口（浮层是**贴在窗口上**的）。
   * 写成 `min()` 是为了**两个上限同时在**：既不超 560，也不出窗口。
   */
  const floatStyle: CSSProperties | undefined =
    mode === 'float'
      ? {
          left: spot?.x,
          top: spot?.y,
          maxHeight: `min(${MONITOR_MAX_HEIGHT}px, calc(100% - 3.25rem))`
        }
      : undefined

  const header = (
    <MonitorCardHeader
      count={face?.count}
      dragProps={headerDragProps}
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

  const content =
    face?.id === 'pending' ? (
      <PendingTicketsFace />
    ) : face?.id === 'mind-pending' ? (
      <MindPendingFace />
    ) : (
      <DossierFace agentId={agentId} />
    )

  if (mode === 'float') {
    /**
     * **浮层那一层挂到 `document.body`**（portal）。
     *
     * 为什么不是 `fixed` 直接给那张卡：`fixed` 的 `left/top` 也是视口坐标，但那样
     * 拖动起点要按**父容器**反算（`floating-position.ts`），父容器成了 body ⇒ body
     * 有没有外边距不确定 ⇒ 起点会差几像素。多一层 `fixed inset-0` 的壳，父容器的
     * rect 恒等于视口 ⇒ 起点算得准。
     */
    return createPortal(
      <div className="pointer-events-none fixed inset-0 z-[1000]" data-monitor-float-layer="">
        <MonitorPanel
          className="pointer-events-auto absolute"
          header={header}
          mode="float"
          style={floatStyle}
          // 拖动定位（`app/shell/floating-position.ts`）按这枚 testid 找浮层**本体**
          // 来算拖动起点（那个元素的父级就是定位容器）⇒ 不能丢。
          // （testid 保持旧名：那一档是别线在飞的文件，本刀不去碰它。）
          testId="dossier-panel"
        >
          {content}
        </MonitorPanel>
      </div>,
      document.body
    )
  }

  return (
    <MonitorPanel
      className={cn(className, 'bg-(--ui-bg-primary)')}
      header={header}
      mode="column"
      testId="dossier-panel"
      // 并排列**不占顶栏**：从顶栏下一行开始，并把顶栏那道线接过去。
      topRow={<MonitorTopBarRow />}
    >
      {content}
    </MonitorPanel>
  )
}
