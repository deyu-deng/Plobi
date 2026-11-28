import { useStore } from '@nanostores/react'
import { useNavigate } from 'react-router-dom'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { ContextUsagePanel } from '@/app/shell/context-usage-panel'
import { Codicon } from '@/components/ui/codicon'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'
import { LiveDuration } from '@/lib/statusbar'
import { $activeSessionId, $busy, $connection, $currentCwd, $currentUsage, $sessionStartedAt, $turnStartedAt } from '@/store/session'

import { SETTINGS_ROUTE } from '../../routes'

/**
 * 输入框下面那一行（用户 10-31，照 Qoder）：**左边**是工作目录与本地/远程，**右边**是上下文容量条 + 百分比。
 *
 * ⚠️ **这一行现在是「活的」**（用户 11-28 00:1x：「顺便把对话框下面的组件变的可以交互。这是之前留的坑」）：
 *
 * 1. **左边第一格是时长**（这一轮已用时间 ＋ 会话已用时间）——它**从窗口那条状态栏搬过来的**
 *    （那条状态栏**退役了**：只有卡片这一副布局了 ⇒ 它在卡片布局下本来就一个都没有）。
 * 2. **工作目录那一枚可以点**：本地 ⇒ **在系统文件管理器里打开那个目录**（走现成的
 *    `openExternal(file://…)` → 主进程 `shell.openPath`）；远端 ⇒ 那个目录在另一台机器上，
 *    本机打不开 ⇒ 那一枚**照旧是字**（tooltip 说清它是远端的）。
 * 3. **本地／远程那一枚可以点**：跳到设置里的**网关**那一段（这条连接是怎么配的）。
 *
 * 右边那枚上下文容量条**照旧**（点它还是开那个面板）——它本来就可点。
 */
export function ComposerContextFooter() {
  const { t } = useI18n()
  const cwd = useStore($currentCwd)
  const usage = useStore($currentUsage)
  const connection = useStore($connection)
  const sessionId = useStore($activeSessionId)
  const busy = useStore($busy)
  const sessionStartedAt = useStore($sessionStartedAt)
  const turnStartedAt = useStore($turnStartedAt)
  const { requestGateway } = useGatewayRequest()
  const navigate = useNavigate()

  const folder = cwd.trim().split(/[\\/]/).filter(Boolean).pop() ?? ''
  const remote = connection?.mode === 'remote'

  const percent =
    usage.context_max && Number.isFinite(usage.context_percent)
      ? Math.max(0, Math.min(100, Math.round(usage.context_percent ?? 0)))
      : null

  /** 本地 ⇒ 在系统文件管理器里打开那个目录（远端的目录在本机打不开）。 */
  const openFolder = () => {
    if (remote || !cwd.trim()) {
      return
    }

    const slashed = cwd.trim().replace(/\\/g, '/').replace(/^\/+/, '')
    const encoded = slashed.split('/').map(part => encodeURIComponent(part)).join('/')

    void window.plobiDesktop?.openExternal?.(`file:///${encoded.replace(/^([A-Za-z])%3A/i, '$1:')}`)
  }

  return (
    <div
      className="mt-1 flex min-w-0 items-center gap-3 px-1 text-[0.68rem] text-(--ui-text-tertiary)"
      data-slot="composer-footer"
    >
      {/* 时长（用户 11-28 00:1x：放在这条上下文条的左边）。这一轮**跑的时候**才在。 */}
      <span
        className="flex shrink-0 items-center gap-1 tabular-nums"
        data-slot="composer-footer-turn"
        title={t.shell.statusbar.currentTurnElapsed}
      >
        {busy && turnStartedAt ? <Loader2 className="size-3 animate-spin" /> : null}
        <LiveDuration since={turnStartedAt} />
      </span>
      <span
        className="flex shrink-0 items-center gap-1 tabular-nums"
        data-slot="composer-footer-session"
        title={t.shell.statusbar.runtimeSessionElapsed}
      >
        <Codicon name="history" size="0.75rem" />
        <LiveDuration since={sessionStartedAt} />
      </span>
      {remote ? (
        <span className="flex min-w-0 items-center gap-1" title={t.composer.footer.remoteFolder}>
          <Codicon name="folder" size="0.75rem" />
          <span className="truncate">{folder || '—'}</span>
        </span>
      ) : (
        <button
          className="flex min-w-0 items-center gap-1 rounded px-1 py-0.5 hover:bg-(--chrome-action-hover) hover:text-foreground"
          data-slot="composer-footer-cwd"
          onClick={openFolder}
          title={t.composer.footer.openFolder}
          type="button"
        >
          <Codicon name="folder" size="0.75rem" />
          <span className="truncate">{folder || '—'}</span>
        </button>
      )}
      <button
        className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 hover:bg-(--chrome-action-hover) hover:text-foreground"
        data-slot="composer-footer-connection"
        onClick={() => navigate(`${SETTINGS_ROUTE}?tab=gateway`)}
        title={t.settings.nav.gateway}
        type="button"
      >
        <Codicon name={remote ? 'remote' : 'device-desktop'} size="0.75rem" />
        <span>{remote ? t.composer.footer.remote : t.composer.footer.local}</span>
      </button>
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
