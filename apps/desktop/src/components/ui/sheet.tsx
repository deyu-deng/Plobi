'use client'

import { Dialog as SheetPrimitive } from 'radix-ui'
import * as React from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

function Sheet({ ...props }: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />
}

function SheetTrigger({ ...props }: React.ComponentProps<typeof SheetPrimitive.Trigger>) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />
}

function SheetClose({ ...props }: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />
}

function SheetPortal({ ...props }: React.ComponentProps<typeof SheetPrimitive.Portal>) {
  return <SheetPrimitive.Portal data-slot="sheet-portal" {...props} />
}

function SheetOverlay({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Overlay>) {
  return (
    <SheetPrimitive.Overlay
      className={cn(
        'fixed inset-0 z-50 bg-black/22 backdrop-blur-[0.125rem] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0',
        className
      )}
      data-slot="sheet-overlay"
      {...props}
    />
  )
}

function SheetContent({
  className,
  children,
  side = 'right',
  showCloseButton = true,
  belowTitlebar = false,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> & {
  side?: 'top' | 'right' | 'bottom' | 'left'
  showCloseButton?: boolean
  /**
   * 侧边抽屉让开窗口顶栏那一条（Windows/WSLg 把原生的最小化/最大化/关闭画在顶上
   * 34px 里，永远压在网页之上）。不让开的话，抽屉的标题与那颗关闭按钮会正好落在
   * 系统按钮底下（用户 11-19 21:5x 指着截图说「和整个窗口的关闭按钮重合了」）。
   *
   * 做法与右栏一致（`app/right-sidebar/index.tsx` 的 `pt-(--titlebar-height)`）：
   * 底色照旧铺满全高，只是内容与关闭按钮从顶栏下面开始。
   *
   * 抽屉是 portal 到 `body` 的，`--titlebar-height` 定义在 AppShell 的
   * SidebarProvider 上、这里继承不到 ⇒ 必须带兜底值（34 与 `app/shell/titlebar.ts`
   * 的 TITLEBAR_HEIGHT 同值）。
   */
  belowTitlebar?: boolean
}) {
  const { t } = useI18n()

  return (
    <SheetPortal>
      <SheetOverlay />
      <SheetPrimitive.Content
        className={cn(
          'fixed z-50 flex flex-col gap-3 border-(--ui-stroke-secondary) bg-(--ui-sidebar-surface-background) text-[length:var(--conversation-text-font-size)] shadow-md transition ease-in-out data-[state=closed]:animate-out data-[state=closed]:duration-300 data-[state=open]:animate-in data-[state=open]:duration-500',
          side === 'right' &&
            'inset-y-0 right-0 h-full w-3/4 border-l data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right sm:max-w-sm',
          side === 'left' &&
            'inset-y-0 left-0 h-full w-3/4 border-r data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left sm:max-w-sm',
          side === 'top' &&
            'inset-x-0 top-0 h-auto border-b data-[state=closed]:slide-out-to-top data-[state=open]:slide-in-from-top',
          side === 'bottom' &&
            'inset-x-0 bottom-0 h-auto border-t data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom',
          belowTitlebar && 'pt-[var(--titlebar-height,34px)]',
          className
        )}
        data-slot="sheet-content"
        {...props}
      >
        {children}
        {showCloseButton && (
          <SheetPrimitive.Close
            aria-label={t.common.close}
            className={cn(
              'absolute right-3 rounded-md p-1 text-(--ui-text-tertiary) opacity-70 ring-offset-background transition-opacity hover:bg-(--chrome-action-hover) hover:text-foreground hover:opacity-100 focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:outline-hidden disabled:pointer-events-none data-[state=open]:bg-secondary',
              belowTitlebar ? 'top-[calc(var(--titlebar-height,34px)+0.75rem)]' : 'top-3'
            )}
          >
            <Codicon name="close" size="1rem" />
            <span className="sr-only">{t.common.close}</span>
          </SheetPrimitive.Close>
        )}
      </SheetPrimitive.Content>
    </SheetPortal>
  )
}

function SheetHeader({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('flex flex-col gap-1 p-3', className)} data-slot="sheet-header" {...props} />
}

function SheetFooter({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('mt-auto flex flex-col gap-2 p-3', className)} data-slot="sheet-footer" {...props} />
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      className={cn('text-[0.9375rem] font-semibold text-foreground', className)}
      data-slot="sheet-title"
      {...props}
    />
  )
}

function SheetDescription({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      className={cn(
        'text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-(--ui-text-tertiary)',
        className
      )}
      data-slot="sheet-description"
      {...props}
    />
  )
}

export { Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger }
