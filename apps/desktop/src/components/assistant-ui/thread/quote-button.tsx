import type { FC, PointerEvent as ReactPointerEvent } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import { setQuoteTarget } from '@/store/quote'

/**
 * 气泡上的「引用」入口（裁定 196 ④）：把这条消息挂到输入框上方，发送时带上
 * `reply_to`。图标按钮、无文案——同一把锤子挂在用户气泡/外部气泡/助手回复三处。
 *
 * 有选区时只引选区（2026-10-29 用户令「引用文字不能只能整段引」）：选区必须是
 * **这条消息文本的子串**才算数——否则在 A 气泡选中、点 B 气泡的引用会把 A 的话
 * 挂到 B 的 id 上，`reply_to` 就指错了。`pointerdown` 上 preventDefault 是为了让
 * 点按钮这个动作别先把选区点塌（浏览器默认 mousedown 收起选区）。
 */
export const QuoteButton: FC<{ id?: string; text: string; className?: string }> = ({
  id,
  text,
  className
}) => {
  if (!id || !text.trim()) {
    return null
  }

  const quoteSelectedText = () => {
    const selected = window.getSelection()?.toString().trim() ?? ''

    return selected && text.includes(selected) ? selected : text
  }

  return (
    <button
      aria-label="Quote"
      className={cn(
        // 24px 命中区 + 14px 字形：与同一条动作行里的停止/复制/刷新同一把尺
        // （用户 11-03：「很多矢量图标大小没有统一」，那一行原来是 20/36 两种）。
        'grid size-6 place-items-center rounded-sm text-(--ui-text-quaternary) transition-colors hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)',
        className
      )}
      onClick={() => setQuoteTarget({ id, text: quoteSelectedText() })}
      onPointerDown={event => {
        if (event.button === 0) {
          event.preventDefault()
        }
      }}
      type="button"
    >
      <Codicon name="comment" size="0.875rem" />
    </button>
  )
}
