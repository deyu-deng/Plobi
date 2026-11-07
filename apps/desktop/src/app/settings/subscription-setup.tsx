import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { Download, ExternalLink, Loader2, RefreshCw } from '@/lib/icons'

import type { LoginCard } from './quota-login-cards'

/**
 * First-run setup for one subscription card (2026-11-07).
 *
 * The card's `+` used to call the sign-in endpoint directly, which only worked on a
 * machine where the software was already installed and signed in by hand. On a new
 * Windows box every leg is `not_installed`, so the `+` had nothing to do. This dialog
 * is the missing half: it reads the hub's `state` and walks the smallest path that can
 * end in a working switch —
 *
 *   not installed (CLI, we have a recipe)   → install → login
 *   not installed (CLI, no recipe)          → official page → recheck → login
 *   not installed (desktop app)             → official page → recheck → login
 *   installed, not signed in                → login
 *   cannot reach anything (network)         → send the user to Proxy, not to "config error"
 *
 * Two rulings shape the copy: nothing is claimed that we cannot do (a desktop app is
 * never "auto-installed" — we open its official page and re-check), and every step ends
 * in the same place, a re-read of the catalog, so the row's switch appears on its own
 * instead of being faked locally.
 */

export type SetupPhase = 'download' | 'install' | 'login' | 'manual' | 'network'

/** Reasons that mean "the path out is a node in Proxy", not "your config is wrong". */
const NETWORK_REASONS = new Set([
  'network_unreachable',
  'no_proxy',
  'proxy_not_configured',
  'proxy_unreachable'
])

/**
 * Which step the card is on. Kept as a pure function so the state machine can be
 * reasoned about (and tested) without mounting the dialog: the only inputs are the
 * hub's `state` / `reason_code`, the app kind, and whether a recipe exists.
 */
export function setupPhase(card: LoginCard): SetupPhase {
  if (card.state === 'not_installed') {
    if (card.appKind === 'desktop-app') {
      return 'download'
    }

    return card.installCommand ? 'install' : 'manual'
  }

  if (NETWORK_REASONS.has(card.reasonCode)) {
    return 'network'
  }

  return 'login'
}

interface SubscriptionSetupProps {
  card: LoginCard
  /** Re-read the catalog: the only way a switch is ever allowed to appear. */
  onChanged: () => void
  onClose: () => void
  /**
   * The shell's sign-in path, shared with the switch's own connect handler. A dialog
   * that called the bridge itself would skip the routing bookkeeping that turns the
   * row into a switch, so the caller owns this.
   */
  onLogin?: (appId: string) => Promise<{ error?: string; ok: boolean }>
  /** Jump to Settings → Proxy. The only thing a network failure can ask of the user. */
  onOpenNetwork?: () => void
  open: boolean
}

export function SubscriptionSetupDialog({
  card,
  onChanged,
  onClose,
  onLogin,
  onOpenNetwork,
  open
}: SubscriptionSetupProps) {
  const { t } = useI18n()
  const subs = t.settings.providers.subscriptions
  const s = subs.setup

  const [busy, setBusy] = useState<null | 'install' | 'login' | 'recheck'>(null)
  const [error, setError] = useState<null | string>(null)
  // Once a login has been kicked off, the dialog stops offering the button and starts
  // waiting: the browser half happens outside our window, so "press again" is noise.
  const [started, setStarted] = useState(false)
  const [phase, setPhase] = useState<SetupPhase>(() => setupPhase(card))

  // A fresh card starts a fresh flow; without this, reopening a card would resume the
  // previous one's step (and its busy flag).
  useEffect(() => {
    if (!open) {
      return
    }

    setPhase(setupPhase(card))
    setBusy(null)
    setError(null)
    setStarted(false)
  }, [open, card.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // The catalog is the only truth: whatever step we were on, a card that turned ready
  // is done. This is what makes "install → login → switch" end without a reload.
  useEffect(() => {
    if (open && card.state === 'ready' && started) {
      onClose()
    }
  }, [card.state, open, started, onClose])

  // Leaving `not_installed` behind (because the recheck saw it) advances the flow
  // rather than asking the user to notice it themselves.
  useEffect(() => {
    if (phase === 'download' || phase === 'manual') {
      if (card.state !== 'not_installed') {
        setPhase(setupPhase(card))
      }
    }
  }, [card, phase])

  const openPage = useCallback((url: null | string) => {
    if (url) {
      void window.plobiDesktop?.openExternal?.(url)
    }
  }, [])

  const runInstall = useCallback(async () => {
    const install = window.plobiDesktop?.plobiGateway?.install

    if (!install) {
      setError(s.installUnsupported)

      return
    }

    setBusy('install')
    setError(null)

    try {
      const res = await install(card.id)

      if (res.ok) {
        // The probe is the verdict, not `ok`: an installer that exits 0 without
        // putting the binary on PATH has not installed anything.
        if (res.installed === false) {
          setError(s.installMissingAfter(card.name))
        } else {
          setPhase('login')
          onChanged()
        }
      } else {
        setError(res.detail || s.installFailed(card.name))
      }
    } catch {
      setError(s.installFailed(card.name))
    } finally {
      setBusy(null)
    }
  }, [card.id, card.name, onChanged, onLogin, s])

  const runRecheck = useCallback(() => {
    setBusy('recheck')
    setError(null)
    onChanged()
    // The parent re-reads the catalog; give it a beat so the spinner is not a lie, and
    // if the app is still missing say so instead of silently staying put.
    window.setTimeout(() => {
      setBusy(null)
      setError(s.recheckMissing(card.name))
    }, 1200)
  }, [card.name, onChanged, s])

  const runLogin = useCallback(async () => {
    const auth = onLogin ?? window.plobiDesktop?.plobiGateway?.auth

    if (!auth) {
      setError(s.loginUnsupported(card.name))

      return
    }

    setBusy('login')
    setError(null)

    try {
      const res = await auth(card.id)

      if (res.ok) {
        setStarted(true)
        onChanged()
      } else {
        // A leg whose sign-in command we do not have is not a config error — it is a
        // "we cannot drive this one yet". Say that, and offer the manual route.
        setError(res.error || s.loginFailed(card.name))
      }
    } catch {
      setError(s.loginFailed(card.name))
    } finally {
      setBusy(null)
    }
  }, [card.id, card.name, onChanged, s])

  const methodLabel = subs.setup.method as Record<string, string | undefined>

  return (
    <Dialog onOpenChange={next => (next ? undefined : onClose())} open={open}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{s.title(card.name)}</DialogTitle>
          <DialogDescription>
            {phase === 'install'
              ? s.installBody(card.name)
              : phase === 'download'
                ? s.downloadBody(card.name)
                : phase === 'manual'
                  ? s.manualBody(card.name)
                  : phase === 'network'
                    ? s.networkBody
                    : s.loginBody(card.name, methodLabel[card.loginMethod] ?? methodLabel.unknown ?? '')}
          </DialogDescription>
        </DialogHeader>

        {/* A desktop app is never installed by us: the page is the honest offer, and
            the recheck is how the dialog learns the user finished. */}
        {phase === 'download' ? (
          <div className="grid gap-2">
            <p className="font-mono text-[length:var(--conversation-caption-font-size)] break-all opacity-70">
              {card.download}
            </p>
            <Button onClick={() => openPage(card.download)} size="sm" type="button" variant="secondary">
              <ExternalLink className="size-3.5" />
              {s.downloadAction}
            </Button>
          </div>
        ) : null}

        {phase === 'install' && card.installCommand ? (
          <p className="font-mono text-[length:var(--conversation-caption-font-size)] break-all opacity-70">
            {card.installCommand}
          </p>
        ) : null}

        {phase === 'manual' ? (
          <div className="grid gap-2">
            {card.installCommand ? (
              <p className="font-mono text-[length:var(--conversation-caption-font-size)] break-all opacity-70">
                {card.installCommand}
              </p>
            ) : null}
            {card.download ? (
              <Button onClick={() => openPage(card.download)} size="sm" type="button" variant="secondary">
                <ExternalLink className="size-3.5" />
                {s.downloadAction}
              </Button>
            ) : null}
          </div>
        ) : null}

        {phase === 'network' ? (
          <p className="text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] opacity-70">
            {s.networkHint}
          </p>
        ) : null}

        {busy === 'login' || started ? (
          <p className="flex items-center gap-1.5 text-[length:var(--conversation-caption-font-size)] opacity-70">
            <Loader2 className="size-3.5 animate-spin" />
            {s.loginWaiting}
          </p>
        ) : null}

        {error ? (
          <p
            className="text-[length:var(--conversation-caption-font-size)] leading-[var(--dl-leading-row-desc)] text-destructive"
            role="alert"
          >
            {error}
          </p>
        ) : null}

        <DialogFooter>
          {/* The step's primary action, always in the same corner so the flow reads as
              one button that changes meaning rather than four buttons. */}
          {phase === 'install' ? (
            <Button disabled={busy === 'install'} onClick={() => void runInstall()} size="sm" type="button">
              {busy === 'install' ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
              {busy === 'install' ? s.installing : s.installAction}
            </Button>
          ) : null}

          {phase === 'download' || phase === 'manual' ? (
            <Button disabled={busy === 'recheck'} onClick={runRecheck} size="sm" type="button" variant="secondary">
              {busy === 'recheck' ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
              {busy === 'recheck' ? s.rechecking : s.recheckAction}
            </Button>
          ) : null}

          {phase === 'login' && !started ? (
            <Button disabled={busy === 'login'} onClick={() => void runLogin()} size="sm" type="button">
              {busy === 'login' ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {s.loginAction}
            </Button>
          ) : null}

          {phase === 'network' ? (
            <Button
              onClick={() => {
                onOpenNetwork?.()
                onClose()
              }}
              size="sm"
              type="button"
            >
              {s.networkAction}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
