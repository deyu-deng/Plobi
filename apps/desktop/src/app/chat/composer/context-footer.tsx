import { useStore } from '@nanostores/react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { ContextUsagePanel } from '@/app/shell/context-usage-panel'
import { Codicon } from '@/components/ui/codicon'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import { $activeSessionId, $connection, $currentCwd, $currentUsage } from '@/store/session'

/**
 * 输入框下面那一行（用户 10-31，照 Qoder）：左边是工作目录与本地/远程，
 * 右边是上下文容量条 + 百分比；点它打开原来的上下文面板。
 */
export function ComposerContextFooter() {
  const { t } = useI18n()
  const cwd = useStore($currentCwd)
  const usage = useStore($currentUsage)
  const connection = useStore($connection)
  const sessionId = useStore($activeSessionId)
  const { requestGateway } = useGatewayRequest()

  const folder = cwd.trim().split(/[\\/]/).filter(Boolean).pop() ?? ''
  const remote = connection?.mode === 'remote'
  const percent =
    usage.context_max && Number.isFinite(usage.context_percent)
      ? Math.max(0, Math.min(100, Math.round(usage.context_percent ?? 0)))
      : null

  return (
    <div
      className="mt-1 flex min-w-0 items-center gap-3 px-1 text-[0.68rem] text-(--ui-text-tertiary)"
      data-slot="composer-footer"
    >
      <span className="flex min-w-0 items-center gap-1">
        <Codicon name="folder" size="0.75rem" />
        <span className="truncate">{folder || '—'}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1">
        <Codicon name={remote ? 'remote' : 'device-desktop'} size="0.75rem" />
        <span>{remote ? t.composer.footer.remote : t.composer.footer.local}</span>
      </span>
      {percent === null ? null : (
        <Popover>
          <PopoverTrigger asChild>
            <button
              aria-label={t.composer.footer.context}
              className="ml-auto flex shrink-0 items-center gap-1.5 rounded px-1 py-0.5 hover:bg-(--chrome-action-hover) hover:text-foreground"
              type="button"
            >
              <span className="relative block h-1 w-6 overflow-hidden rounded-full bg-(--ui-stroke-secondary)">
                {/* width 是读数，不是样式常量：容量变了它就得跟着变 */}
                <span className="absolute inset-y-0 left-0 bg-foreground/70" style={{ width: `${percent}%` }} />
              </span>
              <span className="tabular-nums">{percent}%</span>
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="w-auto border-(--ui-stroke-secondary) p-0"
            side="top"
            sideOffset={8}
          >
            <ContextUsagePanel currentUsage={usage} requestGateway={requestGateway} sessionId={sessionId} />
          </PopoverContent>
        </Popover>
      )}
    </div>
  )
}
