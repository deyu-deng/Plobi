import './loader.css'

import { type ComponentProps, type CSSProperties } from 'react'

import { PLOBI_MARK_PATH, PLOBI_MARK_VIEW_BOX } from '@/components/plobi-glyphs'
import { cn } from '@/lib/utils'

/**
 * 加载态：**品牌小乌龟沿一条跑道从左走到右**（用户 11-13 12:35 拍板）。
 *
 * 三件事钉在这一档组件里：
 *
 * 1. **形状只有一只龟**：走的是品牌字形 `PLOBI_MARK_PATH`（`components/plobi-glyphs.ts`
 *    那张表生成的龟侧影），**不新画一只**——屏上认得出的是「那只龟」，画第二只就漂了。
 * 2. **走位是纯 CSS**（`transform`），组件里**没有 `requestAnimationFrame`**。
 *    上一刀 `WP-CHAT-CARD-FLICKER` 的病根正是**旧实现**那枚粒子加载指示：它每帧重算
 *    一条 220 步的 SVG 路径、并把 40 个粒子的 `cx/cy/r/opacity` 逐个改写（实测约
 *    1.7 万次 DOM 改写/秒，窗口最小化时照改 ⇒ 屏上「一直在闪」）。装饰动画不该逐帧
 *    改 DOM。
 * 3. **紧凑档**（`dot`）：格子只有 16–24px 时画不下跑道，那一档不画跑道、乌龟原地
 *    起伏。形状统一，走法随空间而变。
 *
 * 循环走，**不表达真实进度**（主人 11-13 12:4x 拍板）——没有真实进度可表达的地方
 * 就不假装有。
 */

export type LoaderSize = 'dot' | 'lg' | 'md' | 'sm'

/** 龟的大小与跑道长度。跑道至少要有一个龟身宽，否则读不出「在走」。 */
const LOADER_SIZES: Record<LoaderSize, { size: string; width: string }> = {
  dot: { size: '16px', width: '16px' },
  lg: { size: '24px', width: '12rem' },
  md: { size: '18px', width: '9rem' },
  sm: { size: '12px', width: '5rem' }
}

export interface LoaderProps extends Omit<ComponentProps<'div'>, 'children'> {
  /** 读屏念的那一句（不是屏上可见文案）。各调用点沿用自己已有的那一句。 */
  label?: string
  size?: LoaderSize
}

type LoaderVars = CSSProperties & { '--loader-size': string; '--loader-width': string }

export function Loader({
  className,
  label = 'Loading',
  role = 'status',
  size = 'md',
  style,
  ...props
}: LoaderProps) {
  const dims = LOADER_SIZES[size]

  const vars: LoaderVars = {
    ...style,
    '--loader-size': dims.size,
    '--loader-width': dims.width
  }

  return (
    <div
      {...props}
      aria-label={props['aria-label'] ?? label}
      className={cn('loader text-primary', size === 'dot' && 'loader--dot', className)}
      role={role}
      style={vars}
    >
      <div aria-hidden="true" className="loader__track">
        <span className="loader__rail" />
        <div className="loader__runner">
          <span className="loader__trail" />
          <svg
            className="loader__turtle"
            fill="currentColor"
            focusable="false"
            viewBox={PLOBI_MARK_VIEW_BOX}
          >
            <path d={PLOBI_MARK_PATH} fillRule="nonzero" />
          </svg>
        </div>
      </div>
    </div>
  )
}
