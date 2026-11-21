import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { requestComposerFocus, requestComposerInsertRefs } from '@/app/chat/composer/focus'
import { documentLocationRef } from '@/app/chat/composer/inline-refs'
import {
  SelectionToolbar,
  type ExternalSelection,
  type SelectionToolbarAction
} from '@/components/selection-toolbar/selection-toolbar'
import { PageLoader } from '@/components/page-loader'
import { writeClipboardText } from '@/components/ui/copy-button'
import { Codicon } from '@/components/ui/codicon'
import { Copy } from '@/lib/icons'
import { useI18n } from '@/i18n'
import { $currentCwd } from '@/store/session'

import { registerGenOfficeCloseHandler } from './genoffice-sessions'
import {
  officeDiskChangeAction,
  queryGenOfficeEditorDirty,
  registerGenOfficeDirtyProvider
} from './genoffice-dirty'
import type { OfficePreviewLabels } from './office-preview'
import { applyOfficeViewState } from './office-view-state'
import { useFileRevision } from './use-file-revision'

export type GenOfficeKind = 'slides' | 'sheets' | 'word'

interface GenOfficeWebview extends HTMLElement {
  executeJavaScript?: (code: string) => Promise<unknown>
  getWebContentsId?: () => number
  reload?: () => void
}

const EDITOR_PAGE: Record<GenOfficeKind, string> = {
  slides: 'slides.html',
  sheets: 'sheets.html',
  word: 'word.html'
}

const DEV_EDITOR_BASE = 'http://127.0.0.1:5175'

/** 与 scripts/genoffice-selection-bridge.mjs 里的 SELECTION_CHANNEL 对齐。 */
const SELECTION_CHANNEL = 'plobi:selection'

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
  const pendingDiskChangeRef = useRef(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [bridgeMissing, setBridgeMissing] = useState(false)
  const [diskChanged, setDiskChanged] = useState(false)
  const [selection, setSelection] = useState<null | ExternalSelection>(null)
  const { t } = useI18n()
  const { change: diskChange } = useFileRevision(path)

  // webview 里选区的 rect 是相对 webview 视口的；浮窗是主页面 fixed 定位，
  // 得加上 webview 元素在主页面的偏移。宿主收一次消息算一次，滚动时 rect 会重发。
  const onSelectionMessage = useCallback((value: unknown) => {
    if (!value || typeof value !== 'object') {
      setSelection(null)
      return
    }
    const { text, rect } = value as {
      rect?: { bottom: number; left: number; right: number; top: number }
      text?: string
    }
    const webview = webviewRef.current

    if (!text || !text.trim() || !rect || !webview) {
      setSelection(null)
      return
    }
    const host = webview.getBoundingClientRect()

    setSelection({
      rect: {
        bottom: host.top + rect.bottom,
        left: host.left + rect.left,
        right: host.left + rect.right,
        top: host.top + rect.top
      },
      text: text.trim()
    })
  }, [])

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

  useEffect(
    () =>
      registerGenOfficeDirtyProvider(path, () =>
        queryGenOfficeEditorDirty(kind, webContentsIdRef.current)
      ),
    [kind, path]
  )

  useEffect(() => {
    if (diskChange === 0) {
      return
    }

    let cancelled = false

    const evaluate = async () => {
      const webview = webviewRef.current
      const active = document.activeElement
      const focused =
        webviewFocusedRef.current || Boolean(webview && (active === webview || webview.contains(active)))
      const dirty = await queryGenOfficeEditorDirty(kind, webContentsIdRef.current)

      if (cancelled) {
        return
      }

      const action = officeDiskChangeAction(focused, dirty)

      if (action === 'reload') {
        pendingDiskChangeRef.current = false
        setDiskChanged(false)
        setReloadKey(value => value + 1)

        return
      }

      if (action === 'prompt') {
        pendingDiskChangeRef.current = false
        setDiskChanged(true)

        return
      }

      pendingDiskChangeRef.current = true
    }

    void evaluate()

    return () => {
      cancelled = true
    }
  }, [diskChange, kind])

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
        webview.className = 'flex h-full w-full border-0 bg-white'
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
        const onStop = () => {
          setLoading(false)
          // 页面自己的 load 事件比文档解析早，脚本内部会等正文渲染出来再还原位置。
          applyOfficeViewState(webview, kind, path)
        }
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

          if (!pendingDiskChangeRef.current) {
            return
          }

          pendingDiskChangeRef.current = false
          void queryGenOfficeEditorDirty(kind, webContentsIdRef.current).then(dirty => {
            if (dirty === true) {
              setDiskChanged(true)
            }
          })
        }
        const onDomReady = () => {
          webContentsIdRef.current = webview.getWebContentsId?.() ?? null
        }
        const onIpcMessage = (event: Event) => {
          const detail = event as Event & { channel?: string; args?: unknown[] }

          if (detail.channel === SELECTION_CHANNEL) {
            onSelectionMessage(detail.args?.[0])
          }
        }

        webview.addEventListener('did-start-loading', onStart)
        webview.addEventListener('did-stop-loading', onStop)
        webview.addEventListener('did-fail-load', onFail)
        webview.addEventListener('focus', onFocus)
        webview.addEventListener('blur', onBlur)
        webview.addEventListener('dom-ready', onDomReady)
        webview.addEventListener('ipc-message', onIpcMessage)
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
  }, [kind, onSelectionMessage, path, reloadKey])

  const retry = () => {
    pendingDiskChangeRef.current = false
    setDiskChanged(false)
    setFailed(false)
    setLoading(true)
    setReloadKey(value => value + 1)
  }

  const dismissSelection = useCallback(() => setSelection(null), [])

  // 选区浮窗（WP-OFFICE-SELECTION-TOOLBAR 阶段二）：与聊天/源码视图/PDF 是同一份组件，
  // 只是选区从 webview 上报而来。@section 用选中文字的前若干字作锚点——渲染进程内部
  // 的块索引没有对外入口，这是拿得到的、诚实的定位方式（见任务书）。
  const selectionActions = useMemo<SelectionToolbarAction[]>(
    () => [
      {
        id: 'copy',
        icon: <Copy className="size-3.5" />,
        label: t.selectionToolbar.copy,
        onSelect: ({ text }) => {
          void writeClipboardText(text)
        }
      },
      {
        id: 'add-to-chat',
        icon: <Codicon name="comment" size="0.85rem" />,
        label: t.selectionToolbar.addToChat,
        onSelect: ({ text }) => {
          const anchor = text.replace(/\s+/gu, ' ').slice(0, 16)
          const ref = documentLocationRef('section', path, $currentCwd.get(), anchor)
          requestComposerInsertRefs([ref])
          requestComposerFocus('main')
        }
      }
    ],
    [path, t]
  )

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
      <SelectionToolbar
        actions={selectionActions}
        containerRef={hostRef}
        external={selection}
        onExternalDismiss={dismissSelection}
        testId="genoffice-selection-toolbar"
      />

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
