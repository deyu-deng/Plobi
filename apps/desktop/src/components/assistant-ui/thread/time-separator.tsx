import { useAuiState } from '@assistant-ui/react'
import type { FC } from 'react'

/** 两条消息之间超过这个间隔，就插一行时间（微信是同一形状）。 */
export const TIME_GAP_MS = 10 * 60 * 1000

/** 只有跨过这个间隔才值得占一行；第一条消息也放一行（那是这一段对话的起点）。 */
export function shouldShowTimeGap(previous?: Date, current?: Date): boolean {
  if (!current || Number.isNaN(current.getTime())) {
    return false
  }
  if (!previous || Number.isNaN(previous.getTime())) {
    return true
  }
  return current.getTime() - previous.getTime() >= TIME_GAP_MS
}

/**
 * 时间分隔头上的那句：同一天只写时刻，同一年写月日，跨年才带年份。
 * 用 `Intl` 按系统语言格式化——**不硬编中文**（组件里不许有中文文案）。
 */
export function formatThreadTimeGap(current: Date | undefined, now = new Date()): string {
  if (!current || Number.isNaN(current.getTime())) {
    return ''
  }
  const sameDay =
    current.getFullYear() === now.getFullYear() &&
    current.getMonth() === now.getMonth() &&
    current.getDate() === now.getDate()
  const sameYear = current.getFullYear() === now.getFullYear()

  const options: Intl.DateTimeFormatOptions = sameDay
    ? { hour: '2-digit', minute: '2-digit' }
    : sameYear
      ? { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }
      : { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }

  return new Intl.DateTimeFormat(undefined, options).format(current)
}

export const ThreadTimeSeparator: FC<{ index: number }> = ({ index }) => {
  const previous = useAuiState(
    s => (s.thread.messages[index - 1] as { createdAt?: Date } | undefined)?.createdAt,
  )
  const current = useAuiState(
    s => (s.thread.messages[index] as { createdAt?: Date } | undefined)?.createdAt,
  )

  if (!shouldShowTimeGap(previous, current)) {
    return null
  }

  const label = formatThreadTimeGap(current)

  if (!label) {
    return null
  }

  return (
    <div
      className="flex w-full justify-center py-1 text-[0.6875rem] leading-5 text-muted-foreground/60"
      data-slot="aui_time-separator"
    >
      {label}
    </div>
  )
}
