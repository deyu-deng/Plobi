import { useEffect } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

export type WorkbenchLauncherAction = 'agenda' | 'files' | 'review' | 'terminal'

export interface WorkbenchLauncherItem {
  id: WorkbenchLauncherAction
  icon: string
  title: string
  hint?: string
  disabled?: boolean
}

export interface WorkbenchLauncherProps {
  items: readonly WorkbenchLauncherItem[]
  labels: { title: string }
  open: boolean
  onClose: () => void
  onPick: (id: WorkbenchLauncherAction) => void
}

export function WorkbenchLauncher({ items, labels, open, onClose, onPick }: WorkbenchLauncherProps) {
  useEffect(() => {
    if (!open) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      event.preventDefault()
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, open])

  if (!open) {
    return null
  }

  const firstEnabled = items.find(item => !item.disabled)?.id

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-end bg-black/20 p-3 pt-[calc(var(--titlebar-height)+0.5rem)]"
      data-testid="workbench-launcher-backdrop"
      onMouseDown={onClose}
      role="presentation"
    >
      <div
        aria-label={labels.title}
        aria-modal="true"
        className="w-[min(22rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-border bg-popover p-2 text-popover-foreground shadow-2xl"
        data-testid="workbench-launcher"
        onMouseDown={event => event.stopPropagation()}
        role="dialog"
      >
        <p className="px-2 pb-1 pt-1 text-[0.66rem] font-medium tracking-wide text-muted-foreground uppercase">
          {labels.title}
        </p>
        <div className="flex flex-col gap-1">
          {items.map(item => (
            <button
              autoFocus={item.id === firstEnabled}
              className={cn(
                'flex w-full min-w-0 items-center gap-3 rounded-lg border border-transparent px-2 py-2 text-left transition-colors',
                item.disabled
                  ? 'cursor-not-allowed opacity-45'
                  : 'hover:border-border hover:bg-accent focus-visible:border-ring focus-visible:outline-none'
              )}
              data-testid={`workbench-launcher-${item.id}`}
              disabled={item.disabled}
              key={item.id}
              onClick={() => onPick(item.id)}
              type="button"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <Codicon name={item.icon} size="1.05rem" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[0.82rem] font-medium">{item.title}</span>
                {item.hint ? (
                  <span className="block truncate text-[0.66rem] text-muted-foreground">{item.hint}</span>
                ) : null}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
