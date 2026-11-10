import { useCallback, useEffect, useRef, useState } from 'react'

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
 *
 * 还有一件本刀补上的：**它得会自己走掉**。指针离开树行、离开卡片、或者按 Esc，
 * 卡片都要收起来——否则它就成了一块揭不掉的面板（用户 11-10：「一旦弹出就不消失了」）。
 */

export interface HoverPreviewProps {
  /** 正文摘要；没加载出来时显示占位。 */
  text?: string
  title: string
  /** 指针还在树行上（`false` = 已经离开）。 */
  active: boolean
  /** 卡片自己收起时回调（Esc、移开、点「打开」之后）：父层把那一格清掉。 */
  onClose?: () => void
  onOpen?: () => void
}

const HOVER_INTENT_MS = 220
const HOVER_LEAVE_MS = 140

export function HoverPreview({ active, onClose, onOpen, text, title }: HoverPreviewProps) {
  const [visible, setVisible] = useState(false)
  const [overCard, setOverCard] = useState(false)
  const timer = useRef<number | null>(null)
  const leaveTimer = useRef<number | null>(null)

  const clearLeave = useCallback(() => {
    if (leaveTimer.current) {
      window.clearTimeout(leaveTimer.current)
      leaveTimer.current = null
    }
  }, [])

  /** 收卡片：自己藏起来，并告诉父层那一格可以清掉了。 */
  const dismiss = useCallback(() => {
    setVisible(false)
    setOverCard(false)
    onClose?.()
  }, [onClose])

  useEffect(() => {
    if (active || overCard) {
      // 指针还在（树行上、或者已经挪到卡片上）：别收。
      clearLeave()
      if (active && !visible) {
        if (timer.current) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => setVisible(true), HOVER_INTENT_MS)
      }
      return
    }

    // 指针两处都不在了：收。留一小段宽限，指针才来得及从树行挪到卡片上。
    if (timer.current) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    if (visible) {
      leaveTimer.current = window.setTimeout(() => dismiss(), HOVER_LEAVE_MS)
    }
  }, [active, overCard, visible, clearLeave, dismiss])

  // Esc 关（那把刀的判据第 4 条）。收掉之后不抢焦点，键盘还在正文里。
  useEffect(() => {
    if (!visible) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      dismiss()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [visible, dismiss])

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
      onMouseEnter={() => setOverCard(true)}
      onMouseLeave={() => setOverCard(false)}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[0.74rem] font-medium text-foreground">{title}</span>
        {onOpen && (
          <button
            className="shrink-0 rounded px-1 text-[0.66rem] text-(--ui-text-tertiary) transition-colors hover:text-foreground"
            onClick={() => {
              onOpen()
              dismiss()
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
