import { useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'

/**
 * 悬浮预览：鼠标停在目录树的一个笔记上，浮出正文开头若干行。
 *
 * Obsidian 的这个交互解决的是「我想知道这篇讲了什么，但不想离开现在的页」。
 * 对长笔记尤其重要——盘上最短的一篇也有几千字，光看标题猜不出内容。
 *
 * 三个不能省的细节：
 * ① **悬停意图门**：指针划过就发请求会打爆后端，所以停够一会儿才发。
 * ② **不抢焦点**：预览是纯展示，不能把用户的键盘焦点从正文里带走。
 * ③ **能走**：鼠标移上去之后还能移到预览上（否则永远点不到）。
 */

export interface HoverPreviewProps {
  /** 正文摘要；没加载出来时显示占位。 */
  text?: string
  title: string
  /** 鼠标进入触发；`null` 表示不显示。 */
  active: boolean
  onOpen?: () => void
}

const HOVER_INTENT_MS = 220
const HOVER_LEAVE_MS = 140

export function HoverPreview({ active, onOpen, text, title }: HoverPreviewProps) {
  const [visible, setVisible] = useState(false)
  const [pinned, setPinned] = useState(false)
  const timer = useRef<number | null>(null)
  const leaveTimer = useRef<number | null>(null)

  useEffect(() => {
    if (active) {
      if (leaveTimer.current) {
        window.clearTimeout(leaveTimer.current)
        leaveTimer.current = null
      }
      if (!visible) {
        timer.current = window.setTimeout(() => setVisible(true), HOVER_INTENT_MS)
      }
      return
    }

    if (timer.current) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    if (visible && !pinned) {
      leaveTimer.current = window.setTimeout(() => setVisible(false), HOVER_LEAVE_MS)
    }
  }, [active, pinned, visible])

  useEffect(() => {
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
      if (leaveTimer.current) window.clearTimeout(leaveTimer.current)
    }
  }, [])

  if (!visible) return null

  return (
    <div
      className="pointer-events-auto w-72 rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) p-3 shadow-lg"
      data-testid="note-hover-preview"
      onMouseEnter={() => {
        if (leaveTimer.current) {
          window.clearTimeout(leaveTimer.current)
          leaveTimer.current = null
        }
      }}
      onMouseLeave={() => {
        if (!pinned) setVisible(false)
      }}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[0.74rem] font-medium text-foreground">{title}</span>
        {onOpen && (
          <button
            className="shrink-0 rounded px-1 text-[0.66rem] text-(--ui-text-tertiary) transition-colors hover:text-foreground"
            onClick={() => {
              setPinned(true)
              onOpen()
            }}
            type="button"
          >
            打开
          </button>
        )}
      </div>
      <p
        className={cn(
          'line-clamp-4 text-[0.7rem] leading-[1.6] text-(--ui-text-secondary)',
          !text && 'text-(--ui-text-tertiary)'
        )}
      >
        {text || '…'}
      </p>
    </div>
  )
}

/**
 * 挂在目录树一行上用的触发器。
 *
 * 预览卡片用 fixed 定位跟着指针走——放在树里会撑开树、把别的行挤走。
 */
export function HoverPreviewAnchor({
  children,
  onOpen,
  preview,
  title
}: {
  children: React.ReactNode
  onOpen?: () => void
  preview: React.ReactNode
  title: string
}) {
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)

  return (
    <>
      <span
        className="relative"
        onMouseEnter={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          setPosition({ x: rect.right + 8, y: rect.top })
        }}
        onMouseLeave={() => setPosition(null)}
        ref={ref}
      >
        {children}
      </span>
      {position && (
        <div className="fixed z-40" style={{ left: position.x, top: position.y }}>
          {preview}
        </div>
      )}
    </>
  )
}
