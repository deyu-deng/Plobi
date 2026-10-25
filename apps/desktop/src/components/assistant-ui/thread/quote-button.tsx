import type { FC } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import { setQuoteTarget } from '@/store/quote'

/**
 * 气泡上的「引用」入口（裁定 196 ④）：把这条消息挂到输入框上方，发送时带上
 * `reply_to`。图标按钮、无文案——同一把锤子挂在用户气泡/外部气泡/助手回复三处。
 */
export const QuoteButton: FC<{ id?: string; text: string; className?: string }> = ({
  id,
  text,
  className
}) => {
  if (!id || !text.trim()) {
    return null
  }

  return (
    <button
      aria-label="Quote"
      className={cn(
        'grid size-5 place-items-center rounded text-(--ui-text-quaternary) transition-colors hover:bg-(--chrome-action-hover) hover:text-(--ui-text-secondary)',
        className
      )}
      onClick={() => setQuoteTarget({ id, text })}
      title="Quote"
      type="button"
    >
      <Codicon name="comment" size="0.75rem" />
    </button>
  )
}
