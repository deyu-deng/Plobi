import { useCallback, useState } from 'react'

import { useI18n } from '@/i18n'
import { Loader2, RefreshCw } from '@/lib/icons'
import { cn } from '@/lib/utils'

/** Mirrors what `plobi:magpie:update:check` hands back through preload. */
interface MagpieCheckResult {
  ok: boolean
  running: null | string
  latest: null | string
  bytes: number
  updateAvailable: boolean
  reason: null | string
}

/** Mirrors what `plobi:magpie:update:apply` hands back through preload. */
interface MagpieApplyResult {
  ok: boolean
  version: null | string
  started: boolean
  port: null | number
  reason: null | string
}

type Phase =
  | { kind: 'applied'; version: null | string }
  | { kind: 'applying' }
  | { kind: 'checked'; latest: null | string; running: null | string; updateAvailable: boolean }
  | { kind: 'checking' }
  | { kind: 'failed'; reason: string }
  | { kind: 'idle' }

const BUTTON =
  'rounded-md border border-border/60 px-2 py-0.5 text-[0.66rem] transition-colors hover:bg-accent/60 disabled:opacity-50'

/**
 * 侧车健康条旁边的那一格：跑的是哪一版 magpie 适配层，要不要换。
 *
 * 放在网关菜单里、紧挨着「本机未运行的服务」名单，因为这两件事读的是同一个
 * 对象——侧车自己。上面那半截说「谁没在跑」，这一格说「跑的是不是最新」。
 *
 * 两个动作都必须由用户按下才发生：check 只问上游版本号，apply 会拉 74MB 再
 * 重启侧车。主进程只提供入口、从不自己发起（见 electron/main.ts 的
 * plobi:magpie:update:check 上方注释），界面这里也不做定时轮询。
 */
export function MagpieUpdateBlock() {
  const { t } = useI18n()
  const copy = t.shell.statusbar.magpieUpdate
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })

  // No bridge means this window cannot do it at all (an older main process, or
  // a plain-browser dev shell). Better to show nothing than a dead button.
  const bridged = typeof window !== 'undefined' && Boolean(window.plobiDesktop?.checkMagpieUpdate)

  const check = useCallback(async () => {
    setPhase({ kind: 'checking' })

    try {
      const result = (await window.plobiDesktop?.checkMagpieUpdate?.()) as MagpieCheckResult | undefined

      if (!result?.ok) {
        setPhase({ kind: 'failed', reason: result?.reason || copy.unknown })

        return
      }

      setPhase({
        kind: 'checked',
        latest: result.latest,
        running: result.running,
        updateAvailable: result.updateAvailable
      })
    } catch (err) {
      setPhase({ kind: 'failed', reason: String(err) })
    }
  }, [copy.unknown])

  const apply = useCallback(async () => {
    setPhase({ kind: 'applying' })

    try {
      const result = (await window.plobiDesktop?.applyMagpieUpdate?.()) as MagpieApplyResult | undefined

      // A failed apply still leaves the old build running (the main process
      // keeps the pointer put when the checksum does not match, and the sidecar
      // falls back to the bundled copy when the new binary will not start), so
      // the note can just say what happened without scaring the user.
      if (!result?.ok) {
        setPhase({ kind: 'failed', reason: result?.reason || copy.unknown })

        return
      }

      setPhase({ kind: 'applied', version: result.version })
    } catch (err) {
      setPhase({ kind: 'failed', reason: String(err) })
    }
  }, [copy.unknown])

  if (!bridged) {
    return null
  }

  const busy = phase.kind === 'checking' || phase.kind === 'applying'

  const runningVersion =
    phase.kind === 'checked' ? phase.running : phase.kind === 'applied' ? phase.version : null

  let note: null | string = null

  if (phase.kind === 'checking') {
    note = copy.checking
  } else if (phase.kind === 'applying') {
    note = copy.applying
  } else if (phase.kind === 'checked') {
    note = phase.updateAvailable
      ? copy.available(phase.latest ?? copy.unknown)
      : copy.upToDate
  } else if (phase.kind === 'applied') {
    note = copy.applied(phase.version ?? copy.unknown)
  } else if (phase.kind === 'failed') {
    note = copy.failed(phase.reason)
  }

  return (
    <div className="border-t border-border/50 px-3 py-2" data-testid="magpie-update-block">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
          {copy.title}
        </div>
        {runningVersion && (
          <span className="shrink-0 truncate text-[0.66rem] text-muted-foreground">{copy.running(runningVersion)}</span>
        )}
      </div>

      {note && <p className="mt-1 text-[0.66rem] leading-snug text-muted-foreground">{note}</p>}

      <div className="mt-1.5 flex items-center gap-1.5">
        <button
          className={cn(BUTTON, 'flex items-center gap-1 text-primary')}
          data-testid="magpie-check-update"
          disabled={busy}
          onClick={() => void check()}
          type="button"
        >
          {phase.kind === 'checking' ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <RefreshCw className="size-3" />
          )}
          {copy.check}
        </button>

        {(phase.kind === 'applying' || (phase.kind === 'checked' && phase.updateAvailable)) && (
          <button
            className={cn(BUTTON, 'flex items-center gap-1 text-foreground')}
            data-testid="magpie-apply-update"
            disabled={busy}
            onClick={() => void apply()}
            type="button"
          >
            {phase.kind === 'applying' && <Loader2 className="size-3 animate-spin" />}
            {copy.apply}
          </button>
        )}
      </div>
    </div>
  )
}
