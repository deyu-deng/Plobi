import { useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'

export interface WorkspaceTabLabels {
  loading: string
  unavailable: string
  hint: string
  retry: string
}

// The Univer Workspace browser is a separate local app: Vite serves it in dev,
// the workspace server serves the built copy. Both bind loopback only.
const WORKSPACE_URL = import.meta.env.DEV ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:3020'

interface WorkspaceWebview extends HTMLElement {
  reload?: () => void
}

export function WorkspaceTabPanel({ labels }: { labels: WorkspaceTabLabels }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const webviewRef = useRef<WorkspaceWebview | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host) {
      return
    }

    const webview = document.createElement('webview') as WorkspaceWebview
    webview.className = 'block h-full w-full border-0 bg-white'
    webview.setAttribute('partition', 'persist:plobi-workspace')
    webview.setAttribute('src', WORKSPACE_URL)
    webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no,sandbox=yes')
    host.replaceChildren(webview)
    webviewRef.current = webview

    const onStart = () => {
      setLoading(true)
      setFailed(false)
    }
    const onStop = () => {
      setLoading(false)
    }
    const onFail = (event: Event) => {
      const detail = event as Event & { errorCode?: number; isMainFrame?: boolean }
      // -3 is ERR_ABORTED — a superseded navigation, not a page failure.
      if (detail.isMainFrame !== false && detail.errorCode !== -3) {
        setFailed(true)
        setLoading(false)
      }
    }
    const onGone = () => {
      setFailed(true)
      setLoading(false)
    }

    webview.addEventListener('did-start-loading', onStart)
    webview.addEventListener('did-stop-loading', onStop)
    webview.addEventListener('did-fail-load', onFail)
    webview.addEventListener('render-process-gone', onGone)

    return () => {
      webview.removeEventListener('did-start-loading', onStart)
      webview.removeEventListener('did-stop-loading', onStop)
      webview.removeEventListener('did-fail-load', onFail)
      webview.removeEventListener('render-process-gone', onGone)
      webviewRef.current = null
      host.replaceChildren()
    }
  }, [])

  const retry = () => {
    const webview = webviewRef.current

    if (!webview) {
      return
    }

    setFailed(false)
    setLoading(true)
    webview.reload?.()
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1" data-testid="workspace-webview-host" ref={hostRef} />

      {failed ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background px-8 text-center">
          <Codicon name="warning" size="1.4rem" />
          <p className="text-[0.8rem] font-medium text-foreground">{labels.unavailable}</p>
          <p className="max-w-72 text-[0.68rem] text-(--ui-text-tertiary)">{labels.hint}</p>
          <Button onClick={retry} size="xs" variant="ghost">
            <Codicon name="refresh" size="0.8rem" />
            {labels.retry}
          </Button>
        </div>
      ) : loading ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background">
          <PageLoader label={labels.loading} />
        </div>
      ) : null}
    </div>
  )
}
