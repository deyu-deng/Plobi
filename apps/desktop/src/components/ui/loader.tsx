import './loader.css'

import { type ComponentProps, type CSSProperties } from 'react'

import { PLOBI_MARK_PATH } from '@/components/plobi-glyphs'
import { cn } from '@/lib/utils'

/**
 * 加载态：**品牌小乌龟在一条跑道上从左跑到右**，跑到头淡出、**再从左边重新开始**
 * （用户 11-13 12:35 拍板走法；11-17 08:2x 拍「**不要往返跑**」——不折返、不转身走回来）。
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
 * **颜色跟用户选的主题走**（11-17 08:2x 主人拍）：`--ui-accent`（＝ 运行时注入的
 * `--theme-midground`）——换主题小龟就跟着换，不再是跟周围文字同色的 `text-primary`。
 *
 * 循环走，**不表达真实进度**（主人 11-13 12:4x 拍板）——没有真实进度可表达的地方
 * 就不假装有。
 *
 * **屏上不出现任何文字**（11-17 08:2x 主人拍）：这一档只画乌龟；各处要说明在加载什么
 * 的那几句（"Entering …"、"正在检查更新…"）是**调用点旁边的兄弟节点**，不是这一档的。
 */

export type LoaderSize = 'dot' | 'lg' | 'md' | 'sm'

/**
 * 品牌龟在 64 网格里**只占** x=4–58、y=20–47.5（壳顶在 20、扇贝底在 47.5、头在 x=4），
 * 方框上下左右都空着 ⇒ 按方框落座，龟会**悬在跑道上方 4.6px**（比龟身一半还高，
 * 看着像浮着跑）。这里按它的实际外框裁一棵 viewBox：**形状一个点不改**
 * （还是那张 `PLOBI_MARK_PATH`），只是取景框贴紧，脚才真正踩在跑道上。
 *
 * ⚠️ 品牌图形若重出（`scripts/brand_icons.py`），这几个数要重新核一遍。
 */
const TURTLE_VIEW_BOX = '4 20 54 27.5'

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
      className={cn('loader', size === 'dot' && 'loader--dot', className)}
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
            viewBox={TURTLE_VIEW_BOX}
          >
            <path d={PLOBI_MARK_PATH} fillRule="nonzero" />
          </svg>
        </div>
      </div>
    </div>
  )
}
