import { type ReactNode, useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { translateNow } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { cn } from '@/lib/utils'

interface OverlayViewProps {
  children: ReactNode
  onClose: () => void
  closeLabel?: string
  contentClassName?: string
  headerContent?: ReactNode
  rootClassName?: string
}

export function OverlayView({
  children,
  onClose,
  closeLabel = translateNow('common.close'),
  contentClassName,
  headerContent,
  rootClassName
}: OverlayViewProps) {
  const closeOverlay = () => {
    triggerHaptic('close')
    onClose()
  }

  // Esc dismisses every OverlayView-based overlay. Nested Radix dialogs
  // stop propagation themselves, so opening (e.g.) the model picker inside
  // Settings still closes the picker first instead of the underlying overlay.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }

      event.preventDefault()
      triggerHaptic('close')
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      className={cn(
        // 遮罩不糊背景（Qoder 的玻璃是"半透明不模糊"，我们的主窗口画布也不糊：
        // 浮层压上来时后面那层该读得出是什么，不该化成一团）。
        'fixed inset-0 z-50 bg-black/22',
        // Equidistant inset on every side. The top value is driven by the
        // titlebar height so the card clears the OS traffic-lights vertically;
        // since the card top already sits below them, the left needs no extra
        // inset — keeping all sides equal so the card is ~full-width at any size.
        'p-[calc(var(--titlebar-height)+0.625rem)]',
        'sm:p-[calc(var(--titlebar-height)+0.875rem)]'
      )}
      onClick={event => {
        if (event.target === event.currentTarget) {
          closeOverlay()
        }
      }}
      role="presentation"
    >
      <div
        className={cn(
          // 外壳就是主窗口那一张卡：`dl-card` 一份几何（8px squircle + 1px 发丝边 +
          // 同一支阴影）。以前这里是 `rounded-xl border shadow-md`，浮层和外壳两套尺子，
          // 就是用户说的"overlay 各长各的"。（用户 11-02 拍「外壳 + 设置」）
          'dl-card relative flex h-full min-h-0 flex-col overflow-hidden',
          rootClassName
        )}
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-[calc(var(--titlebar-height)+0.1875rem)] [-webkit-app-region:drag]">
          {headerContent && (
            <div className="pointer-events-auto absolute left-1/2 top-[calc(0.5rem+var(--titlebar-height)/2)] -translate-x-1/2 -translate-y-1/2 [-webkit-app-region:no-drag]">
              {headerContent}
            </div>
          )}

          <Button
            aria-label={closeLabel}
            className="pointer-events-auto absolute right-3 top-[calc(0.1875rem+var(--titlebar-height)/2)] -translate-y-1/2 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground [-webkit-app-region:no-drag]"
            onClick={closeOverlay}
            size="icon-titlebar"
            variant="ghost"
          >
            <Codicon name="close" size="1rem" />
          </Button>
        </div>

        {/* No top padding here: the split-layout columns own their own
            titlebar clearance so their backgrounds run flush to the card top
            (otherwise the card surface shows as a gap above the sidebar). */}
        <div className={cn('min-h-0 flex flex-1 flex-col', contentClassName)}>{children}</div>
      </div>
    </div>
  )
}
