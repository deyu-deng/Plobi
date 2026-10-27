import { useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'

import { registerGenOfficeCloseHandler } from './genoffice-sessions'
import type { OfficePreviewLabels } from './office-preview'
import { useFileRevision } from './use-file-revision'

export type GenOfficeKind = 'slides' | 'sheets' | 'word'

interface GenOfficeWebview extends HTMLElement {
  getWebContentsId?: () => number
  reload?: () => void
}

const EDITOR_PAGE: Record<GenOfficeKind, string> = {
  slides: 'slides.html',
  sheets: 'sheets.html',
  word: 'word.html'
}

const DEV_EDITOR_BASE = 'http://127.0.0.1:5175'

function editorUrl(kind: GenOfficeKind): string {
  const page = EDITOR_PAGE[kind]

  return import.meta.env.DEV
    ? `${DEV_EDITOR_BASE}/${page}`
    : new URL(`genoffice/${page}`, window.location.href).href
}

// The preload and renderer are built together into dist/genoffice. Prefer the
// built page next to the preload: a stale Vite optimizer cache (504 Outdated
// Optimize Dep) otherwise leaves the dev webview blank while the shell still
// looks healthy. Rebuild via `npm run build:genoffice` after changing vendor UI.
function builtEditorUrl(preloadUrl: string, kind: GenOfficeKind): string | null {
  const marker = `/${kind}-preload.cjs`

  if (!preloadUrl.includes(marker)) {
    return null
  }

  return preloadUrl.replace(marker, `/${EDITOR_PAGE[kind]}`)
}

export function GenOfficeFileEditor({
  kind,
  labels,
  path
}: {
  kind: GenOfficeKind
  labels: OfficePreviewLabels
  path: string
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const webviewRef = useRef<GenOfficeWebview | null>(null)
  const webviewFocusedRef = useRef(false)
  const webContentsIdRef = useRef<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [bridgeMissing, setBridgeMissing] = useState(false)
  const [diskChanged, setDiskChanged] = useState(false)
  const { change: diskChange } = useFileRevision(path)

  useEffect(
    () =>
      registerGenOfficeCloseHandler(path, async () => {
        const bridge = window.plobiDesktop?.genofficeRequestClose
        const webContentsId = webContentsIdRef.current

        if (!bridge || !webContentsId) {
          return true
        }

        return bridge(kind, webContentsId)
      }),
    [kind, path]
  )

  useEffect(() => {
    if (diskChange === 0) {
      return
    }

    // The editor's own save also bumps mtime. While the webview owns focus,
    // treat the write as the human's and don't nag; an agent edit made while
    // focus is elsewhere still raises the reload bar.
    const webview = webviewRef.current
    const active = document.activeElement

    if (webviewFocusedRef.current || (webview && (active === webview || webview.contains(active)))) {
      return
    }

    setDiskChanged(true)
  }, [diskChange])

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

    void bridge(kind)
      .then(preloadUrl => {
        if (!active || !hostRef.current) {
          return
        }

        const webview = document.createElement('webview') as GenOfficeWebview
        webview.className = 'block h-full w-full border-0 bg-white'
        webview.setAttribute('preload', preloadUrl)
        webview.setAttribute('partition', `persist:plobi-genoffice-${kind}`)
        webview.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no,sandbox=yes')
        const src = builtEditorUrl(preloadUrl, kind) ?? editorUrl(kind)
        webview.setAttribute('src', `${src}?file=${encodeURIComponent(path)}`)
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

        const onFocus = () => {
          webviewFocusedRef.current = true
        }
        const onBlur = () => {
          webviewFocusedRef.current = false
        }
        const onDomReady = () => {
          webContentsIdRef.current = webview.getWebContentsId?.() ?? null
        }

        webview.addEventListener('did-start-loading', onStart)
        webview.addEventListener('did-stop-loading', onStop)
        webview.addEventListener('did-fail-load', onFail)
        webview.addEventListener('focus', onFocus)
        webview.addEventListener('blur', onBlur)
        webview.addEventListener('dom-ready', onDomReady)
      })
      .catch(() => {
        if (active) {
          setBridgeMissing(true)
          setLoading(false)
        }
      })

    return () => {
      active = false
      webviewFocusedRef.current = false
      webContentsIdRef.current = null
      webviewRef.current = null
      host.replaceChildren()
    }
  }, [kind, path, reloadKey])

  const retry = () => {
    setDiskChanged(false)
    setFailed(false)
    setLoading(true)
    setReloadKey(value => value + 1)
  }

  return (
    <div className="relative flex h-full min-h-0 flex-1 flex-col bg-white text-[#111]">
      {diskChanged ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-[#e5c07b] bg-[#fff8e6] px-2 py-1.5 text-[0.68rem] text-[#6b4e00]">
          <Codicon name="warning" size="0.8rem" />
          <span className="min-w-0 flex-1">{labels.diskChanged}</span>
          <button
            className="shrink-0 rounded border border-[#d8b45a] px-1.5 py-0.5 font-medium hover:bg-[#f7e7bd]"
            onClick={retry}
            type="button"
          >
            {labels.reload}
          </button>
          <button
            aria-label={labels.dismiss}
            className="grid size-5 shrink-0 place-items-center rounded hover:bg-[#f7e7bd]"
            onClick={() => setDiskChanged(false)}
            title={labels.dismiss}
            type="button"
          >
            <Codicon name="close" size="0.7rem" />
          </button>
        </div>
      ) : null}
      <div className="min-h-0 flex-1" data-testid="genoffice-file-host" ref={hostRef} />

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
