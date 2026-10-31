import { useState } from 'react'
import type { ReactNode } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { AlertTriangle } from '@/lib/icons'
import { cn } from '@/lib/utils'

// The single canonical error glyph (codicon's filled error mark). Use this
// everywhere an error is surfaced (boundaries, dialogs, banners) so failures
// read identically — one icon, one color, no background chip.
export function ErrorIcon({ className, size = '1.75rem' }: { className?: string; size?: string }) {
  return <Codicon className={cn('text-destructive', className)} name="error" size={size} />
}

// Inline error banner for detail panes (born in Messaging's platform error,
// now shared with the MCP config pane): warn glyph + tinted rounded box.
// For centered full-surface failures use ErrorState below instead.
export function ErrorBanner({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-[length:var(--conversation-caption-font-size)] leading-(--conversation-caption-line-height) text-destructive',
        className
      )}
    >
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 whitespace-pre-wrap break-words">{children}</span>
    </div>
  )
}

export interface ErrorStateProps {
  /** Optional actions row/stack rendered below the copy. */
  children?: ReactNode
  className?: string
  description?: ReactNode
  /** Defaults to a destructive AlertCircle. */
  icon?: ReactNode
  title: ReactNode
}

// Shared, presentation-only error layout: the canonical ErrorIcon (no bg chip)
// over a centered title + body, with an optional actions stack. Used by the
// React error boundary, the in-dialog update error, and the boot-failure banner
// so every failure reads the same. Title/description accept nodes so Radix
// Dialog callers can pass DialogTitle/DialogDescription for accessibility.
export function ErrorState({ children, className, description, icon, title }: ErrorStateProps) {
  return (
    <div className={cn('grid gap-5', className)}>
      <div className="flex flex-col items-center gap-3 text-center">
        {icon ?? <ErrorIcon />}

        {typeof title === 'string' ? (
          <h2 className="text-center text-xl font-semibold tracking-tight">{title}</h2>
        ) : (
          title
        )}

        {typeof description === 'string' ? (
          <p className="max-w-prose text-center text-sm leading-5 text-muted-foreground">{description}</p>
        ) : (
          description
        )}
      </div>

      {children && <div className="grid gap-2">{children}</div>}
    </div>
  )
}

export interface ErrorCardAction {
  label: string
  onClick: () => void
}

export interface ErrorCardProps {
  /** 严重度只分两档：`error` 会拦事，`warning` 只是提醒。 */
  severity?: 'error' | 'warning'
  /** 一行说清"哪件事没成"，不写机制。 */
  title: string
  /** 原始详情（后端那句话/堆栈）。**默认收起**，可复制——它是给修它的人看的。 */
  detail?: null | string
  /** **只有一个**动作。两个动作等于没动作。 */
  action?: ErrorCardAction
  onDismiss?: () => void
  className?: string
}

/**
 * 错误卡（v2）：严重度 + 一句话 + **一个**动作 + 可复制的详情。
 *
 * 设计口径（用户 10-22/10-30 的两句）：屏幕只写"这是什么"，不写"机制怎么运作"；
 * 机器词不上屏。所以标题永远是人话，原始错误只进「详情」那一块，并且能一键复制——
 * 修它的人要的是原文，不是又一句转述。
 *
 * 与既有三件的关系：`ErrorBanner` 仍是"行内一条带背景的小条"（详情本来就在界面上），
 * `ErrorState` 是整面居中的空态；这一张是**带动作与可折叠原文的卡片**，用于"出错了，能做什么"。
 */
export function ErrorCard({
  action,
  className,
  detail,
  onDismiss,
  severity = 'error',
  title
}: ErrorCardProps) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const trimmed = (detail ?? '').trim()
  const tone = severity === 'warning' ? 'text-[color:var(--dl-status-warn,#b5811f)]' : 'text-destructive'

  async function handleCopy() {
    if (!trimmed) {
      return
    }

    try {
      await navigator.clipboard.writeText(trimmed)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // 复制不了就把详情展开让人手选——静默失败比报错更糟。
      setOpen(true)
    }
  }

  return (
    <div
      className={cn('rounded-lg border px-3 py-2.5 text-[length:var(--conversation-caption-font-size)]', className)}
      data-severity={severity}
      data-slot="error-card"
      role={severity === 'warning' ? 'status' : 'alert'}
      style={{
        borderColor: severity === 'warning' ? 'color-mix(in srgb, var(--dl-status-warn, #b5811f) 35%, transparent)' : undefined,
        background:
          severity === 'warning'
            ? 'color-mix(in srgb, var(--dl-status-warn, #b5811f) 8%, transparent)'
            : 'color-mix(in srgb, var(--destructive, #b91c1c) 8%, transparent)'
      }}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className={cn('mt-0.5 size-3.5 shrink-0', tone)} />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-foreground">{title}</div>
          {trimmed ? (
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <button
                className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary) underline-offset-2 hover:underline"
                onClick={() => setOpen(value => !value)}
                type="button"
              >
                {open ? t.common.hideDetails : t.common.errorDetails}
              </button>
              <button
                className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary) underline-offset-2 hover:underline"
                onClick={() => void handleCopy()}
                type="button"
              >
                {copied ? t.common.detailsCopied : t.common.copyDetails}
              </button>
            </div>
          ) : null}
          {open && trimmed ? (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-black/5 p-2 font-mono text-[0.7rem] leading-4 text-(--ui-text-secondary) dark:bg-white/5">
              {trimmed}
            </pre>
          ) : null}
          {action ? (
            <button
              className="mt-2 rounded-md border px-2.5 py-1 text-[length:var(--conversation-caption-font-size)] font-medium hover:bg-black/5 dark:hover:bg-white/10"
              onClick={action.onClick}
              type="button"
            >
              {action.label}
            </button>
          ) : null}
        </div>
        {onDismiss ? (
          <button aria-label="dismiss" className="shrink-0 text-(--ui-text-tertiary) hover:text-foreground" onClick={onDismiss} type="button">
            ✕
          </button>
        ) : null}
      </div>
    </div>
  )
}
