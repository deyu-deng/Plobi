import { useId } from 'react'

import { cn } from '@/lib/utils'

/**
 * Plobi 的 mark 在转（用户 10-21：「会话加载态为什么不用这个，这个很好看呀」）。
 *
 * 以前会话/切换的等待是一串盲文字符在跳（`⠋⠙⠹…`），functional 但不像品牌。
 * 这里用**品牌 mark 本身**做等待指示：两条笔画（一竖一斜）沿同一条蓝→紫渐变，
 * 整枚缓慢自转 + 轻微呼吸——安静、不抢注意力，转起来就知道"在读"。
 *
 * 尺寸由 `className` 给（默认 24px）。`prefers-reduced-motion: reduce` 时
 * 不转（CSS 里关掉动画），只留一枚静态 mark。
 */
export function PlobiMarkSpinner({
  className,
  label,
  size = '1.5rem'
}: {
  className?: string
  /** 读屏用的名字（视觉上不显示）。 */
  label?: string
  /** 边长，CSS 长度（默认 1.5rem）。 */
  size?: string
}) {
  const gradientId = useId()

  return (
    <span
      aria-label={label}
      className={cn('inline-flex shrink-0 items-center justify-center', className)}
      role={label ? 'status' : undefined}
      style={{ height: size, width: size }}
    >
      <svg aria-hidden="true" className="plobi-mark-spin size-full" fill="none" viewBox="0 0 24 24">
        <defs>
          <linearGradient gradientUnits="userSpaceOnUse" id={gradientId} x1="4" x2="21" y1="21" y2="3">
            <stop stopColor="#6C7CF7" />
            <stop offset="1" stopColor="#8B5CF6" />
          </linearGradient>
        </defs>
        {/* λ 的两条腿：一条从上往下的长弧，一条从左下往右上的斜笔，
            交点偏下——和品牌 mark 的比例一致（下方收成一个点）。 */}
        <path
          d="M15.9 3.4c-2.5 4.9-4.3 9.9-5.6 15.1-.2.8-.3 1.5-.4 2.1"
          stroke={`url(#${gradientId})`}
          strokeLinecap="round"
          strokeWidth="2.05"
        />
        <path
          d="M7.1 20.4c2.1-5.6 4.9-10.7 8.4-15.3"
          stroke={`url(#${gradientId})`}
          strokeLinecap="round"
          strokeWidth="2.05"
        />
      </svg>
    </span>
  )
}
