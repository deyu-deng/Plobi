import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Check, ExternalLink, Loader2 } from '@/lib/icons'
import { getMemoryProviderOAuthStatus, startMemoryProviderOAuth } from '@/plobi'
import { notifyError } from '@/store/notifications'
import type { MemoryProviderOAuthStatus } from '@/types/plobi'

const POLL_MS = 1500
const POLL_TIMEOUT_MS = 120_000

// Small connect affordance rendered under the provider dropdown. Capability is
// backend-driven: the status route 404s for providers without an oauth_flow
// module, so non-OAuth providers render nothing.
export function MemoryConnect({ provider }: { provider: string }) {
  const { t } = useI18n()
  const u = t.settings.memory
  const [capable, setCapable] = useState<'no' | 'unknown' | 'yes'>('unknown')
  const [connected, setConnected] = useState(false)
  const [auth, setAuth] = useState<MemoryProviderOAuthStatus['auth']>(null)
  const [phase, setPhase] = useState<'error' | 'idle' | 'pending'>('idle')
  const [detail, setDetail] = useState('')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const deadline = useRef(0)

  const stop = useCallback(() => {
    if (timer.current !== null) {
      clearInterval(timer.current)
      timer.current = null
    }
  }, [])

  useEffect(() => {
    let active = true
    setCapable('unknown')
    getMemoryProviderOAuthStatus(provider)
      .then(s => {
        if (!active) {
          return
        }

        setCapable('yes')
        setConnected(s.connected)
        setAuth(s.auth)
      })
      .catch(() => {
        if (active) {
          setCapable('no')
        }
      })

    return () => {
      active = false
      stop()
    }
  }, [provider, stop])

  // An error message isn't sticky — it clears back to the steady state
  // (Connect link, plus the connected badge if a credential is stored).
  useEffect(() => {
    if (phase !== 'error') {
      return
    }

    const t = setTimeout(() => {
      setPhase('idle')
      setDetail('')
    }, 6000)

    return () => clearTimeout(t)
  }, [phase])

  const connect = useCallback(async () => {
    setPhase('pending')

    try {
      await startMemoryProviderOAuth(provider)
    } catch (err) {
      setPhase('error')
      setDetail(u.connectFailed)
      notifyError(err, u.startFailed)

      return
    }

    deadline.current = Date.now() + POLL_TIMEOUT_MS
    stop()
    timer.current = setInterval(() => {
      void (async () => {
        try {
          const next = await getMemoryProviderOAuthStatus(provider)

          if (next.state === 'pending') {
            if (Date.now() > deadline.current) {
              stop()
              setPhase('error')
              setDetail(u.timedOut)
            }

            return
          }

          stop()
          setConnected(next.connected)
          setAuth(next.auth)

          if (next.state === 'error') {
            setPhase('error')
            setDetail(next.detail || u.connectionFailed)
          } else {
            setPhase('idle')
          }
        } catch {
          // Transient poll failure — keep trying until the deadline.
        }
      })()
    }, POLL_MS)
  }, [provider, stop, u.connectFailed, u.connectionFailed, u.startFailed, u.timedOut])

  const cancel = useCallback(() => {
    stop()
    setPhase('idle')
  }, [stop])

  if (capable !== 'yes') {
    return null
  }

  const connectLabel = connected ? (auth === 'apikey' ? u.connectViaOauth : u.reconnect) : u.connect

  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      {phase === 'idle' && connected && (
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <Check className="size-3" />
          {auth === 'apikey' ? u.apiKeySet : u.oauthSet}
        </span>
      )}
      {phase === 'pending' ? (
        <>
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {u.waitingConsent}
          </span>
          <Button className="h-auto p-0 text-xs" onClick={cancel} size="sm" type="button" variant="link">
            {t.common.cancel}
          </Button>
        </>
      ) : (
        <Button
          className="h-auto gap-1 p-0 text-xs"
          onClick={() => void connect()}
          size="sm"
          type="button"
          variant="link"
        >
          <ExternalLink className="size-3" />
          {connectLabel}
        </Button>
      )}
      {phase === 'error' && detail && <span className="text-destructive">{detail}</span>}
    </span>
  )
}
