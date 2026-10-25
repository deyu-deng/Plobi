import { useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'

import type { OfficePreviewLabels } from './office-preview'

interface GenOfficeWebview extends HTMLElement {
  reload?: () => void
}

const editorBaseUrl = import.meta.env.DEV
  ? 'http://127.0.0.1:5175/sheets.html'
  : new URL('genoffice/sheets.html', window.location.href).href

export function GenOfficeSheetEditor({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const webviewRef = useRef<GenOfficeWebview | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [bridgeMissing, setBridgeMissing] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    let active = true
    setLoading(true)
    setFailed(false)
    setBridgeMissing(false)

    const bridge = window.plobiDesktop?.genofficePreloadUrl

    if (!bridge) {
      setBridgeMissing(true)
      setLoading(false)

      return
    }

    void bridge()
      .then(preloadUrl => {
        if (!active || !hostRef.current) {
          return
        }

        const webview = document.createElement('webview') as GenOfficeWebview
        webview.className = 'block h-full w-full border-0 bg-white'
        webview.setAttribute('preload', preloadUrl)
        webview.setAttribute('partition', 'persist:plobi-genoffice')
        webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no,sandbox=yes')
        webview.setAttribute('src', `${editorBaseUrl}?file=${encodeURIComponent(path)}`)
        host.replaceChildren(webview)
        webviewRef.current = webview

        const onStart = () => {
          setLoading(true)
          setFailed(false)
        }
        const onStop = () => setLoading(false)
        const onFail = (event: Event) => {
          const detail = event as Event & { errorCode?: number; isMainFrame?: boolean }

          if (detail.isMainFrame !== false && detail.errorCode !== -3) {
            setFailed(true)
            setLoading(false)
          }
        }

        webview.addEventListener('did-start-loading', onStart)
        webview.addEventListener('did-stop-loading', onStop)
        webview.addEventListener('did-fail-load', onFail)
      })
      .catch(() => {
        if (active) {
          setBridgeMissing(true)
          setLoading(false)
        }
      })

    return () => {
      active = false
      webviewRef.current = null
      host.replaceChildren()
    }
  }, [path, reloadKey])

  const retry = () => {
    setFailed(false)
    setLoading(true)
    setReloadKey(value => value + 1)
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col bg-white text-[#111]">
      <div className="min-h-0 flex-1" data-testid="genoffice-sheet-host" ref={hostRef} />

      {failed || bridgeMissing ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-white px-6 text-center">
          <Codicon name="warning" size="1.25rem" />
          <p className="text-[0.8rem] font-medium text-foreground">{labels.unavailable}</p>
          <p className="text-[0.66rem] text-(--ui-text-tertiary)">{labels.hint}</p>
          <button
            className="rounded border border-[#d1d5db] px-2 py-1 text-[0.68rem] text-[#333] hover:bg-[#f3f4f6]"
            onClick={retry}
            type="button"
          >
            {labels.reload}
          </button>
        </div>
      ) : loading ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-white">
          <PageLoader label={labels.loading} />
        </div>
      ) : null}
    </div>
  )
}
