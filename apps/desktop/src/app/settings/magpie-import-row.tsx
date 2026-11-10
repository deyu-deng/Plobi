import { useCallback, useEffect, useState } from 'react'

import { useI18n } from '@/i18n'
import { Download } from '@/lib/icons'
import type { RuntimeReadinessRequester } from '@/lib/runtime-readiness'
import { activeGateway, ensureActiveGatewayOpen } from '@/store/gateway'

interface ImportScanEntry {
  bytes: number
  name: string
}

interface ImportScanSource {
  dirs: ImportScanEntry[]
  files: ImportScanEntry[]
  importable: boolean
  path: string
}

interface ImportScanResult {
  imported_from?: null | string
  sources: ImportScanSource[]
  target: string
}

interface ImportApplyResult {
  copied?: number
  dirs?: string[]
  error?: null | string
  files?: string[]
  from?: string
  ok?: boolean
  provider_configured?: boolean
  subscription?: boolean
}

type ImportPhase = { kind: 'done'; noSubscription: boolean } | { kind: 'failed' } | { kind: 'idle' } | { kind: 'nothing' }

/** Settings has no onboarding ctx, so it asks the live gateway directly. */
export const gatewayRequester: RuntimeReadinessRequester = async <T,>(method: string, params?: Record<string, unknown>) => {
  let gateway = activeGateway()

  if (!gateway || gateway.connectionState !== 'open') {
    gateway = await ensureActiveGatewayOpen()
  }

  if (!gateway) {
    throw new Error('Plobi gateway is not connected')
  }

  return gateway.request<T>(method, params)
}

/**
 * One click for `I already paid for Claude — why am I signing in again?`.
 *
 * The gateway reads what this machine already has (see `magpie_import.py`) and
 * copies it into Plobi's own magpie directory; nothing is moved and nothing is
 * signed out at the source. The sidecar is then restarted, because magpie reads
 * plugins and sign-ins once at boot — without that the user finishes onboarding
 * against a gateway that still has no providers, which looks like a failure.
 */
export function useMagpieImport(requestGateway: RuntimeReadinessRequester, onImported?: () => void) {
  const [sources, setSources] = useState<ImportScanSource[]>([])
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState<ImportPhase>({ kind: 'idle' })

  // Without the desktop bridge there is no gateway to ask — a browser preview or a
  // unit test must not open a connection just to be told so.
  const bridged = typeof window !== 'undefined' && Boolean(window.plobiDesktop?.restartMagpie)

  useEffect(() => {
    let alive = true

    if (!bridged) {
      setSources([])

      return undefined
    }

    void requestGateway<ImportScanResult>('magpie.import_scan')
      .then(scan => {
        if (alive) {
          setSources((scan?.sources ?? []).filter(source => source.importable))
        }
      })
      .catch(() => {
        // An older gateway has no such RPC: the row simply does not appear.
        if (alive) {
          setSources([])
        }
      })

    return () => {
      alive = false
    }
  }, [bridged, requestGateway])

  const run = useCallback(async () => {
    if (busy || !bridged) {
      return
    }

    setBusy(true)

    try {
      const result = await requestGateway<ImportApplyResult>('magpie.import_apply', {})

      if (!result?.ok) {
        setPhase({ kind: 'nothing' })

        return
      }

      await window.plobiDesktop?.restartMagpie?.()
      setPhase({ kind: 'done', noSubscription: result.subscription === false })
      onImported?.()
    } catch {
      setPhase({ kind: 'failed' })
    } finally {
      setBusy(false)
    }
  }, [bridged, busy, onImported, requestGateway])

  return { bridged, busy, phase, run, sources }
}

function ImportNote({ phase }: { phase: ImportPhase }) {
  const { t } = useI18n()

  if (phase.kind === 'done') {
    return (
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {t.onboarding.importExistingDone}
        {phase.noSubscription ? ` — ${t.onboarding.importExistingNoSubscription}` : null}
      </p>
    )
  }

  if (phase.kind === 'nothing') {
    return <p className="mt-1 text-xs leading-5 text-muted-foreground">{t.onboarding.importExistingNothing}</p>
  }

  if (phase.kind === 'failed') {
    return <p className="mt-1 text-xs leading-5 text-muted-foreground">{t.onboarding.importExistingFailed}</p>
  }

  return <p className="mt-1 text-xs leading-5 text-muted-foreground">{t.onboarding.importExistingPitch}</p>
}

/** The row itself. Renders nothing when this machine has nothing to hand over. */
export function MagpieImportRow({
  onImported,
  requestGateway
}: {
  onImported?: () => void
  requestGateway: RuntimeReadinessRequester
}) {
  const { t } = useI18n()
  const { bridged, busy, phase, run, sources } = useMagpieImport(requestGateway, onImported)

  if (!bridged || (sources.length === 0 && phase.kind === 'idle')) {
    return null
  }

  return (
    <button
      className="group flex w-full items-center justify-between gap-4 rounded-lg border border-primary/25 px-3 py-2.5 text-left transition-colors hover:bg-primary/10 disabled:opacity-60"
      data-testid="magpie-import-row"
      disabled={busy}
      onClick={() => void run()}
      type="button"
    >
      <div className="min-w-0">
        <span className="text-[length:var(--conversation-text-font-size)] font-semibold">
          {busy ? t.onboarding.importExistingRunning : t.onboarding.importExisting}
        </span>
        <ImportNote phase={phase} />
      </div>
      <Download className="size-4 shrink-0 text-primary transition group-hover:translate-y-0.5" />
    </button>
  )
}
