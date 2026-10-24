import { useEffect, useMemo, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

import { Codicon } from '@/components/ui/codicon'
import type { PreviewTarget } from '@/store/preview'

import { fileKindForPath } from './file-kind'
import { readSheetModel, SHEET_MAX_COLS, SHEET_MAX_ROWS } from './sheet-model'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

export interface OfficePreviewLabels {
  loading: string
  unavailable: string
  readOnly: string
  truncated: (rows: number, cols: number) => string
  sheet: string
}

interface FileBytesState {
  bytes: null | Uint8Array
  error: boolean
  loading: boolean
}

async function readFileBytes(path: string): Promise<Uint8Array> {
  const dataUrl = await window.plobiDesktop.readFileDataUrl(path)
  const comma = dataUrl.indexOf(',')

  if (comma < 0) {
    throw new Error('Invalid data URL')
  }

  const header = dataUrl.slice(0, comma)
  const payload = dataUrl.slice(comma + 1)

  if (header.includes(';base64')) {
    const binary = atob(payload)
    const bytes = new Uint8Array(binary.length)

    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }

    return bytes
  }

  return new TextEncoder().encode(decodeURIComponent(payload))
}

function useFileBytes(path: string): FileBytesState {
  const [state, setState] = useState<FileBytesState>({ bytes: null, error: false, loading: true })

  useEffect(() => {
    let active = true
    setState({ bytes: null, error: false, loading: true })

    void readFileBytes(path)
      .then(bytes => {
        if (active) {
          setState({ bytes, error: false, loading: false })
        }
      })
      .catch(() => {
        if (active) {
          setState({ bytes: null, error: true, loading: false })
        }
      })

    return () => {
      active = false
    }
  }, [path])

  return state
}

function PreviewStatus({ error, labels, loading }: { error: boolean; labels: OfficePreviewLabels; loading: boolean }) {
  if (!loading && !error) {
    return null
  }

  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-(--ui-sidebar-surface-background)/90 px-4 text-center">
      {error ? (
        <>
          <Codicon name="warning" size="1.1rem" />
          <p className="text-[0.72rem] text-muted-foreground">{labels.unavailable}</p>
        </>
      ) : (
        <>
          <Codicon name="loading" size="1.1rem" spinning />
          <p className="text-[0.72rem] text-muted-foreground">{labels.loading}</p>
        </>
      )}
    </div>
  )
}

function SheetPreview({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const { bytes, error, loading } = useFileBytes(path)
  const [sheetName, setSheetName] = useState('')
  const model = useMemo(() => (bytes ? readSheetModel(bytes, sheetName || undefined) : null), [bytes, sheetName])
  const selected = model && model.names.includes(sheetName) ? sheetName : (model?.names[0] ?? '')

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-(--ui-stroke-tertiary) px-1.5">
        <span className="shrink-0 text-[0.6rem] text-muted-foreground uppercase">{labels.sheet}</span>
        <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {(model?.names ?? []).map(name => (
            <button
              className={
                name === selected
                  ? 'shrink-0 rounded bg-muted px-1.5 py-0.5 text-[0.64rem] text-foreground'
                  : 'shrink-0 rounded px-1.5 py-0.5 text-[0.64rem] text-muted-foreground hover:bg-muted/60'
              }
              key={name}
              onClick={() => setSheetName(name)}
              type="button"
            >
              {name}
            </button>
          ))}
        </div>
        <span className="shrink-0 text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="border-collapse text-[0.68rem]">
          <tbody>
            {(model?.rows ?? []).map((row, rowIndex) => (
              <tr className="even:bg-muted/20" key={rowIndex}>
                <th className="sticky left-0 z-10 border border-(--ui-stroke-tertiary) bg-(--ui-sidebar-surface-background) px-1.5 py-0.5 text-right font-normal text-muted-foreground">
                  {rowIndex + 1}
                </th>
                {row.map((cell, cellIndex) => (
                  <td
                    className="max-w-64 border border-(--ui-stroke-tertiary) px-1.5 py-0.5 align-top whitespace-pre-wrap"
                    key={cellIndex}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {model?.truncated ? (
          <p className="px-2 py-1 text-[0.62rem] text-muted-foreground">
            {labels.truncated(SHEET_MAX_ROWS, SHEET_MAX_COLS)}
          </p>
        ) : null}
      </div>

      <PreviewStatus error={error} labels={labels} loading={loading} />
    </div>
  )
}

function PdfPreview({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const { bytes, error, loading } = useFileBytes(path)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [rendering, setRendering] = useState(false)
  const [renderError, setRenderError] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host || !bytes) {
      return
    }

    let cancelled = false
    let pdf: pdfjsLib.PDFDocumentProxy | null = null
    host.replaceChildren()
    setRendering(true)
    setRenderError(false)

    void (async () => {
      try {
        pdf = await pdfjsLib.getDocument({ data: bytes }).promise

        if (cancelled) {
          return
        }

        for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
          if (cancelled) {
            break
          }

          const page = await pdf.getPage(pageNumber)
          const viewport = page.getViewport({ scale: 1.2 })
          const canvas = document.createElement('canvas')
          canvas.className = 'mx-auto mb-3 block h-auto max-w-full shadow-sm'
          canvas.width = viewport.width
          canvas.height = viewport.height
          host.append(canvas)
          await page.render({ canvasContext: canvas.getContext('2d')!, viewport }).promise
        }
      } catch {
        if (!cancelled) {
          setRenderError(true)
        }
      } finally {
        if (!cancelled) {
          setRendering(false)
        }
      }
    })()

    return () => {
      cancelled = true
      host.replaceChildren()
      void pdf?.destroy()
    }
  }, [bytes])

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex h-8 shrink-0 items-center justify-end border-b border-(--ui-stroke-tertiary) px-2">
        <span className="text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2" ref={hostRef} />
      <PreviewStatus error={error || renderError} labels={labels} loading={loading || rendering} />
    </div>
  )
}

function DocxPreview({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const { bytes, error, loading } = useFileBytes(path)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [rendering, setRendering] = useState(false)
  const [renderError, setRenderError] = useState(false)

  useEffect(() => {
    const host = hostRef.current

    if (!host || !bytes) {
      return
    }

    let cancelled = false
    host.replaceChildren()
    setRendering(true)
    setRenderError(false)

    void renderAsync(bytes, host, undefined, {
      breakPages: true,
      className: 'docx-preview',
      ignoreHeight: true,
      ignoreWidth: true,
      inWrapper: true,
      useBase64URL: true
    })
      .catch(() => {
        if (!cancelled) {
          setRenderError(true)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRendering(false)
        }
      })

    return () => {
      cancelled = true
      host.replaceChildren()
    }
  }, [bytes])

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex h-8 shrink-0 items-center justify-end border-b border-(--ui-stroke-tertiary) px-2">
        <span className="text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/20 p-2" ref={hostRef} />
      <PreviewStatus error={error || renderError} labels={labels} loading={loading || rendering} />
    </div>
  )
}

export function OfficeFilePreview({ labels, target }: { labels: OfficePreviewLabels; target: PreviewTarget }) {
  const path = target.path ?? ''
  const kind = fileKindForPath(path)

  if (kind === 'sheet') {
    return <SheetPreview labels={labels} path={path} />
  }

  if (kind === 'pdf') {
    return <PdfPreview labels={labels} path={path} />
  }

  if (kind === 'docx') {
    return <DocxPreview labels={labels} path={path} />
  }

  return null
}
