import { useEffect, useRef, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'

export interface BrowserTabLabels {
  placeholder: string
  back: string
  forward: string
  reload: string
  go: string
  newTabTitle: string
  newTabBody: string
  loadFailed: string
  invalidUrl: string
}

interface BrowserWebview extends HTMLElement {
  loadURL?: (url: string) => void
  goBack?: () => void
  goForward?: () => void
  reload?: () => void
  canGoBack?: () => boolean
  canGoForward?: () => boolean
}

export function normalizeBrowserUrl(raw: string): string {
  const value = raw.trim()

  if (!value) {
    return ''
  }

  if (/^https?:\/\//iu.test(value)) {
    return value
  }

  if (/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/.*)?$/iu.test(value)) {
    return `http://${value}`
  }

  if (/^[a-z][a-z0-9+.-]*:/iu.test(value)) {
    return ''
  }

  return `https://${value}`
}

export function BrowserTabPanel({ labels }: { labels: BrowserTabLabels }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const webviewRef = useRef<BrowserWebview | null>(null)
  const [url, setUrl] = useState('')
  const [currentUrl, setCurrentUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const [canBack, setCanBack] = useState(false)
  const [canForward, setCanForward] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    const webview = document.createElement('webview') as BrowserWebview
    webview.className = 'block h-full w-full border-0 bg-background'
    webview.setAttribute('partition', 'persist:plobi-browser')
    webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no,sandbox=yes')
    webview.style.display = 'none'
    host.replaceChildren(webview)
    webviewRef.current = webview

    const refreshNavigation = () => {
      setCanBack(Boolean(webview.canGoBack?.()))
      setCanForward(Boolean(webview.canGoForward?.()))
    }

    const onStart = () => {
      setLoading(true)
      setError(null)
    }
    const onStop = () => {
      setLoading(false)
      refreshNavigation()
    }
    const onNavigate = (event: Event) => {
      const url = (event as Event & { url?: string }).url

      if (url) {
        setCurrentUrl(url)
        setUrl(url)
      }

      refreshNavigation()
    }
    const onFail = (event: Event) => {
      const detail = event as Event & { errorCode?: number; isMainFrame?: boolean }
      // -3 is ERR_ABORTED — a superseded navigation, not a page failure.
      if (detail.isMainFrame !== false && detail.errorCode !== -3) {
        setError(labels.loadFailed)
        setLoading(false)
      }
    }

    webview.addEventListener('did-start-loading', onStart)
    webview.addEventListener('did-stop-loading', onStop)
    webview.addEventListener('did-navigate', onNavigate)
    webview.addEventListener('did-navigate-in-page', onNavigate)
    webview.addEventListener('dom-ready', refreshNavigation)
    webview.addEventListener('did-fail-load', onFail)

    return () => {
      webview.removeEventListener('did-start-loading', onStart)
      webview.removeEventListener('did-stop-loading', onStop)
      webview.removeEventListener('did-navigate', onNavigate)
      webview.removeEventListener('did-navigate-in-page', onNavigate)
      webview.removeEventListener('dom-ready', refreshNavigation)
      webview.removeEventListener('did-fail-load', onFail)
      webviewRef.current = null
      host.replaceChildren()
    }
  }, [labels.loadFailed])

  const navigate = (raw: string) => {
    const next = normalizeBrowserUrl(raw)

    if (!next) {
      setError(labels.invalidUrl)

      return
    }

    const webview = webviewRef.current
    setError(null)
    setCurrentUrl(next)
    setUrl(next)
    setLoading(true)

    if (webview) {
      webview.style.display = 'block'
      webview.loadURL?.(next)
    }
  }

  const refresh = () => {
    webviewRef.current?.reload?.()
    setCanBack(Boolean(webviewRef.current?.canGoBack?.()))
    setCanForward(Boolean(webviewRef.current?.canGoForward?.()))
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <form
        className="flex h-9 shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-1.5"
        data-testid="browser-form"
        onSubmit={event => {
          event.preventDefault()
          navigate(url)
        }}
      >
        <button
          aria-label={labels.back}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-muted disabled:opacity-35"
          disabled={!canBack}
          onClick={() => webviewRef.current?.goBack?.()}
          title={labels.back}
          type="button"
        >
          <Codicon name="arrow-left" size="0.85rem" />
        </button>
        <button
          aria-label={labels.forward}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-muted disabled:opacity-35"
          disabled={!canForward}
          onClick={() => webviewRef.current?.goForward?.()}
          title={labels.forward}
          type="button"
        >
          <Codicon name="arrow-right" size="0.85rem" />
        </button>
        <button
          aria-label={labels.reload}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-muted disabled:opacity-35"
          disabled={!currentUrl}
          onClick={refresh}
          title={labels.reload}
          type="button"
        >
          <Codicon name={loading ? 'loading' : 'refresh'} size="0.85rem" spinning={loading} />
        </button>
        <input
          aria-label={labels.placeholder}
          className="h-6 min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-background px-2 text-[0.7rem] text-foreground outline-none placeholder:text-(--ui-text-tertiary) focus:border-(--ui-accent)"
          onChange={event => setUrl(event.target.value)}
          placeholder={labels.placeholder}
          spellCheck={false}
          value={url}
        />
        <button
          aria-label={labels.go}
          className="grid size-6 place-items-center rounded text-muted-foreground hover:bg-muted"
          title={labels.go}
          type="submit"
        >
          <Codicon name="arrow-right" size="0.85rem" />
        </button>
      </form>

      {error ? <p className="px-2 py-1 text-[0.66rem] text-destructive">{error}</p> : null}

      <div className="relative min-h-0 flex-1">
        <div
          className="absolute inset-0"
          data-testid="browser-webview-host"
          hidden={!currentUrl}
          ref={hostRef}
        />
        {!currentUrl ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center">
            <Codicon name="globe" size="1.4rem" />
            <p className="text-[0.8rem] font-medium text-foreground">{labels.newTabTitle}</p>
            <p className="text-[0.68rem] text-(--ui-text-tertiary)">{labels.newTabBody}</p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
