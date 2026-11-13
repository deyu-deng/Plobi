import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { Button } from '@/components/ui/button'
import { writeClipboardText } from '@/components/ui/copy-button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { ErrorIcon, ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import type { DesktopReleaseInfo, DesktopUpdateCommit, DesktopUpdateStage, DesktopUpdateStatus } from '@/global'
import { useI18n } from '@/i18n'
import { buildCommitChangelog, type CommitGroup } from '@/lib/commit-changelog'
import { AlertCircle, Check, Copy, ExternalLink, Loader2, Terminal } from '@/lib/icons'
import { resolveUpdateCopy, type UpdateTarget } from '@/lib/update-copy'
import { cn } from '@/lib/utils'
import {
  $backendUpdateApply,
  $backendUpdateChecking,
  $backendUpdateStatus,
  $desktopRelease,
  $releaseInstalling,
  $updateApply,
  $updateChecking,
  $updateOverlayOpen,
  $updateOverlayTarget,
  $updateSource,
  $updateStatus,
  applyBackendUpdate,
  applyUpdates,
  checkBackendUpdates,
  checkDesktopRelease,
  checkUpdates,
  installDesktopRelease,
  resetUpdateApplyState,
  setUpdateOverlayOpen,
  type UpdateApplyState
} from '@/store/updates'

import { useStaleAfter } from './hooks/use-stale-after'

function totalItems(groups: readonly CommitGroup[]) {
  return groups.reduce((sum, g) => sum + g.items.length, 0)
}

export function UpdatesOverlay() {
  const open = useStore($updateOverlayOpen)
  const target = useStore($updateOverlayTarget)

  const clientStatus = useStore($updateStatus)
  const clientChecking = useStore($updateChecking)
  const clientApply = useStore($updateApply)
  const backendStatus = useStore($backendUpdateStatus)
  const backendChecking = useStore($backendUpdateChecking)
  const backendApply = useStore($backendUpdateApply)
  const release = useStore($desktopRelease)
  const releaseInstalling = useStore($releaseInstalling)
  const source = useStore($updateSource)

  const isBackend = target === 'backend'
  const status = isBackend ? backendStatus : clientStatus
  const checking = isBackend ? backendChecking : clientChecking
  const apply = isBackend ? backendApply : clientApply
  const check = isBackend ? checkBackendUpdates : checkUpdates
  const install = isBackend ? applyBackendUpdate : applyUpdates

  useEffect(() => {
    if (open && !status && !checking) {
      void check()
    }
  }, [check, checking, open, status])

  // 「有没有新版的壳」是另一条查法（GitHub 上有没有发布物），跟家目录那个
  // checkout 落后多少笔不是一回事。开着这一屏就顺手问一次，省得用户以为
  // 「已是最新」就真的没有新版。
  useEffect(() => {
    if (open && !isBackend) {
      void checkDesktopRelease()
    }
  }, [isBackend, open])

  const behind = status?.behind ?? 0
  const updateAvailable = status?.updateAvailable || behind > 0

  const phase: 'idle' | 'applying' | 'manual' | 'guiSkew' | 'error' =
    apply.stage === 'manual'
      ? 'manual'
      : apply.stage === 'guiSkew'
        ? 'guiSkew'
        : apply.applying || apply.stage === 'restart'
          ? 'applying'
          : apply.stage === 'error'
            ? 'error'
            : 'idle'

  const handleClose = (next: boolean) => {
    if (phase === 'applying') {
      return
    }

    setUpdateOverlayOpen(next)

    if (
      !next &&
      (apply.stage === 'error' || apply.stage === 'restart' || apply.stage === 'manual' || apply.stage === 'guiSkew')
    ) {
      resetUpdateApplyState()
    }
  }

  const handleInstall = () => {
    void install()
  }

  // 错误态上那个「重试」重跑**同一件事**：装壳失败要是被丢去重跑 checkout
  // 的更新，就把用户推到另一条路上去了。
  const handleRetry = () => {
    if (source === 'release') {
      void installDesktopRelease()

      return
    }

    handleInstall()
  }

  return (
    <Dialog onOpenChange={handleClose} open={open}>
      <DialogContent className="max-w-sm overflow-hidden p-0 gap-0" showCloseButton={phase !== 'applying'}>
        {phase === 'applying' && <ApplyingView apply={apply} isBackend={isBackend} />}

        {phase === 'manual' && (
          <ManualView command={apply.command ?? null} message={apply.message} onDone={() => handleClose(false)} />
        )}

        {phase === 'guiSkew' && <GuiSkewView message={apply.message} onDone={() => handleClose(false)} />}

        {phase === 'error' && (
          <ErrorView message={apply.message} onDismiss={() => handleClose(false)} onRetry={handleRetry} />
        )}

        {phase === 'idle' &&
          (isBackend || !release?.newer ? (
            <IdleView
              behind={behind}
              checking={checking}
              commits={status?.commits ?? []}
              onInstall={handleInstall}
              onLater={() => handleClose(false)}
              onRetryCheck={() => void check()}
              status={status}
              target={target}
              updateAvailable={updateAvailable}
            />
          ) : (
            <ReleaseView
              installing={releaseInstalling}
              onInstall={() => void installDesktopRelease()}
              onLater={() => handleClose(false)}
              release={release}
            />
          ))}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  behind,
  checking,
  commits,
  onInstall,
  onLater,
  onRetryCheck,
  status,
  target,
  updateAvailable
}: {
  behind: number
  checking: boolean
  commits: readonly DesktopUpdateCommit[]
  onInstall: () => void
  onLater: () => void
  onRetryCheck: () => void
  status: DesktopUpdateStatus | null
  target: UpdateTarget
  updateAvailable: boolean
}) {
  const { t } = useI18n()
  const u = t.updates

  if (!status && checking) {
    return (
      <CenteredStatus
        icon={<Loader label={u.checking} size="md" />}
        title={u.checking}
      />
    )
  }

  if (!status) {
    return (
      <CenteredStatus
        action={
          <Button onClick={onRetryCheck} size="sm">
            {u.tryAgain}
          </Button>
        }
        icon={<ErrorIcon />}
        title={u.checkFailedTitle}
      />
    )
  }

  if (!status.supported) {
    return (
      <CenteredStatus
        body={status.message ?? u.unsupportedMessage}
        icon={<AlertCircle className="size-6 text-muted-foreground" />}
        title={u.notAvailableTitle}
      />
    )
  }

  if (status.error) {
    return (
      <CenteredStatus
        action={
          <Button disabled={checking} onClick={onRetryCheck} size="sm">
            {u.tryAgain}
          </Button>
        }
        body={u.connectionRetry}
        icon={<ErrorIcon />}
        title={u.checkFailedTitle}
      />
    )
  }

  if (!updateAvailable) {
    return (
      <CenteredStatus
        body={target === 'backend' ? u.latestBodyBackend : u.latestBody}
        icon={<BrandMark className="size-12" />}
        title={u.allSetTitle}
      />
    )
  }

  const groups = buildCommitChangelog(commits)
  const shownItems = totalItems(groups)
  const remaining = Math.max(0, behind - shownItems)

  // Name what's being updated. In remote mode the overlay acts on the connected
  // backend, not the local client — say so. When there are no commit rows to
  // show (e.g. pip/non-git backend), degrade to honest "no release notes" copy
  // instead of generic filler.
  const { title, body } = resolveUpdateCopy({ target, shownItems, copy: u })

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <BrandMark className="size-16" />

        <DialogTitle className="text-center text-xl">{title}</DialogTitle>
        <DialogDescription className="text-center text-sm">{body}</DialogDescription>
      </div>

      <div className="grid gap-3">
        {groups.map(group => (
          <div key={group.id}>
            <p className="text-[0.625rem] font-semibold uppercase tracking-wide text-muted-foreground">{group.label}</p>
            <ul className="mt-1.5 grid gap-1.5 text-xs text-foreground">
              {group.items.map(item => (
                <li className="flex items-start gap-2" key={item}>
                  <span aria-hidden className="mt-1.5 inline-block size-1 shrink-0 rounded-full bg-primary" />
                  <span className="leading-snug">{item}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="grid gap-2">
        <Button className="font-semibold" onClick={onInstall} size="lg">
          {u.updateNow}
        </Button>
        <Button className="font-medium" onClick={onLater} type="button" variant="text">
          {u.maybeLater}
        </Button>
      </div>

      {remaining > 0 && <p className="text-center text-xs text-muted-foreground">{u.moreChanges(remaining)}</p>}
    </div>
  )
}

/**
 * 「有新版的壳」那一屏。跟 IdleView 是两件事：IdleView 说的是家目录里那个
 * checkout 离 origin/main 差几笔（后端），这一屏说的是**用户眼前这个程序**
 * 有没有更新的发布物（壳）。有新版时它排在前面——因为它那一按顺手把后端也
 * 带到同一个版本号上（裁定 208：一个产品一条号）。
 */
function ReleaseView({
  installing,
  onInstall,
  onLater,
  release
}: {
  installing: boolean
  onInstall: () => void
  onLater: () => void
  release: DesktopReleaseInfo
}) {
  const { t } = useI18n()
  const u = t.updates
  const a = t.settings.about
  const version = release.latest ?? ''
  const notesUrl = release.htmlUrl

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <BrandMark className="size-16" />

        <DialogTitle className="text-center text-xl">{a.updateReadyVersion(version)}</DialogTitle>
        <DialogDescription className="text-center text-sm">
          {release.canUpdate ? u.availableBody : u.unsupportedMessage}
        </DialogDescription>
      </div>

      <div className="grid gap-2">
        {release.canUpdate ? (
          <Button className="font-semibold" disabled={installing} onClick={onInstall} size="lg">
            {installing ? <Loader2 className="size-4 animate-spin" /> : null}
            {a.upgradeTo(version)}
          </Button>
        ) : null}

        {notesUrl ? (
          <Button asChild className="font-medium" variant="text">
            <a
              href={notesUrl}
              onClick={event => {
                event.preventDefault()
                void window.plobiDesktop?.openExternal?.(notesUrl)
              }}
              rel="noreferrer"
              target="_blank"
            >
              <ExternalLink className="size-3" />
              {a.releaseNotes}
            </a>
          </Button>
        ) : null}

        <Button className="font-medium" onClick={onLater} type="button" variant="text">
          {u.maybeLater}
        </Button>
      </div>
    </div>
  )
}

function ManualView({ command, message, onDone }: { command: string | null; message?: string; onDone: () => void }) {
  const { t } = useI18n()
  const u = t.updates
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    if (!command) {
      return
    }

    void writeClipboardText(command).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    })
  }

  // No command (e.g. the Linux sandbox-blocked relaunch): render the explanatory
  // message + a Done button, not a copy-a-command box.
  if (!command) {
    return (
      <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <Terminal className="size-8 text-primary" />

          <DialogTitle className="text-center text-xl">{u.manualTitle}</DialogTitle>
          <DialogDescription className="text-center text-sm">{message || u.manualPickedUp}</DialogDescription>
        </div>

        <Button className="font-semibold" onClick={onDone} size="lg" variant="secondary">
          {u.done}
        </Button>
      </div>
    )
  }

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <Terminal className="size-8 text-primary" />

        <DialogTitle className="text-center text-xl">{u.manualTitle}</DialogTitle>
        <DialogDescription className="text-center text-sm">{u.manualBody}</DialogDescription>
      </div>

      <button
        className={cn(
          'group flex w-full items-center justify-between gap-3 rounded-md border px-4 py-3 text-left transition-colors',
          copied ? 'border-primary/50' : 'border-(--stroke-nous) hover:border-(--ui-stroke-secondary)'
        )}
        onClick={handleCopy}
        type="button"
      >
        <code className="min-w-0 flex-1 truncate select-all font-mono text-sm text-foreground">
          <span className="select-none text-muted-foreground">$ </span>
          {command}
        </code>
        <span
          className={cn(
            'flex shrink-0 items-center gap-1 text-xs font-medium transition-colors',
            copied ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground'
          )}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? u.copied : u.copy}
        </span>
      </button>

      <p className="text-center text-xs text-muted-foreground">{u.manualPickedUp}</p>

      <Button className="font-semibold" onClick={onDone} size="lg" variant="secondary">
        {u.done}
      </Button>
    </div>
  )
}

// Linux GUI/backend skew (#45205): backend updated, but the running desktop app
// package (AppImage/.deb/.rpm) was NOT changed. Closeable terminal state that
// tells the user to update/reinstall the desktop app — never claims the GUI was
// updated.
function GuiSkewView({ message, onDone }: { message?: string; onDone: () => void }) {
  const { t } = useI18n()
  const u = t.updates

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <AlertCircle className="size-8 text-amber-500" />

        <DialogTitle className="text-center text-xl">{u.guiSkewTitle}</DialogTitle>
        <DialogDescription className="max-w-prose text-center text-sm leading-5 text-muted-foreground">
          {message || u.guiSkewBody}
        </DialogDescription>
      </div>

      <Button className="font-semibold" onClick={onDone} size="lg" variant="secondary">
        {u.done}
      </Button>
    </div>
  )
}

function ApplyingView({ apply, isBackend }: { apply: UpdateApplyState; isBackend: boolean }) {
  const { t } = useI18n()
  const u = t.updates
  const label = u.stages[apply.stage as DesktopUpdateStage] ?? u.stages.idle
  const body = isBackend ? u.applyingBodyBackend : u.applyingBody
  const currentMessage = apply.message.trim()
  const recentLog = apply.log.slice(-4)

  const percent =
    typeof apply.percent === 'number' && Number.isFinite(apply.percent)
      ? Math.max(2, Math.min(100, Math.round(apply.percent)))
      : null

  // 进度拿不到时那条脉冲是无限动画：等过 8 秒就停脉冲、明说"还没回来"，
  // 否则一个 30 秒才超时的请求会让整个弹层看起来一直在闪（10-26 用户报）。
  const stale = useStaleAfter(percent === null, 8000)

  return (
    <div className="grid gap-5 px-6 pb-6 pt-7">
      <div className="flex flex-col items-center gap-3 text-center">
        <Loader label={label} size="lg" />

        <DialogTitle className="text-center text-xl">{label}</DialogTitle>
        <DialogDescription className="text-center text-sm">{body}</DialogDescription>

        {currentMessage ? (
          <p className="max-w-lg break-words text-center text-xs leading-5 text-muted-foreground">{currentMessage}</p>
        ) : null}
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full bg-primary transition-[width] duration-300 ease-out',
            percent === null && (stale ? 'w-1/3 bg-foreground/30' : 'w-1/3 animate-pulse')
          )}
          style={percent !== null ? { width: `${percent}%` } : undefined}
        />
      </div>
      {stale ? (
        <p className="-mt-3 text-center text-xs text-muted-foreground">
          还没回来 · Still waiting — 这个弹层不再闪了，等后台回答或直接关掉
        </p>
      ) : null}

      {recentLog.length > 1 ? (
        <div className="max-h-24 overflow-hidden rounded-md border border-border/70 bg-muted/35 px-3 py-2 text-left font-mono text-[11px] leading-4 text-muted-foreground">
          {recentLog.map((entry, index) => (
            <div className="truncate" key={`${entry.at}-${index}`}>
              {entry.message}
            </div>
          ))}
        </div>
      ) : null}

      <p className="text-center text-xs text-muted-foreground">{u.applyingClose}</p>
    </div>
  )
}

function ErrorView({ message, onDismiss, onRetry }: { message: string; onDismiss: () => void; onRetry: () => void }) {
  const { t } = useI18n()
  const u = t.updates

  return (
    <ErrorState
      className="px-6 pb-6 pt-7 pr-8"
      description={
        <DialogDescription className="max-w-prose text-center text-sm leading-5 text-muted-foreground">
          {message || u.errorBody}
        </DialogDescription>
      }
      title={<DialogTitle className="text-center text-xl font-semibold tracking-tight">{u.errorTitle}</DialogTitle>}
    >
      <Button className="font-semibold" onClick={onRetry} size="lg">
        {u.tryAgain}
      </Button>
      <Button onClick={onDismiss} variant="text">
        {u.notNow}
      </Button>
    </ErrorState>
  )
}

function CenteredStatus({
  action,
  body,
  icon,
  title
}: {
  action?: React.ReactNode
  body?: string
  icon: React.ReactNode
  title: string
}) {
  return (
    <div className="grid gap-4 px-6 pb-6 pt-8 pr-8">
      <div className="flex flex-col items-center gap-3 text-center">
        {icon}

        <DialogTitle className="text-center text-lg">{title}</DialogTitle>
        {body && <DialogDescription className="text-center text-sm">{body}</DialogDescription>}
      </div>

      {action && <div className="flex justify-center">{action}</div>}
    </div>
  )
}
