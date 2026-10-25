import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'
import type { PreviewTarget } from '@/store/preview'

import { fileKindForPath } from './file-kind'
import { useFileBytes } from './file-bytes'
import { readSheetModel, SHEET_MAX_COLS, SHEET_MAX_ROWS } from './sheet-model'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

const BetterSheetEditor = lazy(async () => ({ default: (await import('./better-sheet')).BetterSheetEditor }))

export interface OfficePreviewLabels {
  loading: string
  unavailable: string
  readOnly: string
  truncated: (rows: number, cols: number) => string
  sheet: string
  legacyNote: string
  save: string
  saving: string
  saved: string
  cancel: string
  conflict: string
  saveFailed: string
  reload: string
  review: string
}

function PreviewStatus({
  error,
  labels,
  loading,
  neutral = false
}: {
  error: boolean
  labels: OfficePreviewLabels
  loading: boolean
  neutral?: boolean
}) {
  if (!loading && !error) {
    return null
  }

  return (
    <div
      className={cn(
        'absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 px-4 text-center',
        neutral ? 'bg-white/90 text-[#555]' : 'bg-(--ui-sidebar-surface-background)/90'
      )}
    >
      {error ? (
        <>
          <Codicon name="warning" size="1.1rem" />
          <p className={neutral ? 'text-[0.72rem] text-[#555]' : 'text-[0.72rem] text-muted-foreground'}>
            {labels.unavailable}
          </p>
        </>
      ) : (
        <>
          <Codicon name="loading" size="1.1rem" spinning />
          <p className={neutral ? 'text-[0.72rem] text-[#555]' : 'text-[0.72rem] text-muted-foreground'}>
            {labels.loading}
          </p>
        </>
      )}
    </div>
  )
}

function columnName(index: number): string {
  let name = ''
  let value = index

  while (value > 0) {
    const remainder = (value - 1) % 26
    name = String.fromCharCode(65 + remainder) + name
    value = Math.floor((value - 1) / 26)
  }

  return name
}

function SheetPreview({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const { bytes, error, loading } = useFileBytes(path)
  const [sheetName, setSheetName] = useState('')
  const model = useMemo(() => (bytes ? readSheetModel(bytes, sheetName || undefined) : null), [bytes, sheetName])
  const selected = model && model.names.includes(sheetName) ? sheetName : (model?.names[0] ?? '')
  const legacy = /\.xls$/iu.test(path)

  return (
    <div className="relative flex min-h-0 flex-1 flex-col bg-white text-[#111]">
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-[#e5e7eb] bg-[#f8fafc] px-1.5">
        <span className="shrink-0 text-[0.6rem] text-[#666] uppercase">{labels.sheet}</span>
        <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {(model?.names ?? []).map(name => (
            <button
              className={
                name === selected
                  ? 'shrink-0 rounded bg-white px-1.5 py-0.5 text-[0.64rem] text-[#111] shadow-sm ring-1 ring-[#d1d5db]'
                  : 'shrink-0 rounded px-1.5 py-0.5 text-[0.64rem] text-[#666] hover:bg-[#eef2f7]'
              }
              key={name}
              onClick={() => setSheetName(name)}
              type="button"
            >
              {name}
            </button>
          ))}
        </div>
        <span className="shrink-0 text-[0.6rem] text-[#777]">{labels.readOnly}</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-white">
        <table className="border-collapse text-[0.68rem]">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 border border-[#d1d5db] bg-[#f3f4f6] px-1.5 py-0.5" />
              {Array.from({ length: Math.max(1, model?.rows[0]?.length ?? 1) }, (_, index) => (
                <th
                  className="sticky top-0 z-10 min-w-16 border border-[#d1d5db] bg-[#f3f4f6] px-1.5 py-0.5 text-center font-normal text-[#666]"
                  key={index}
                >
                  {columnName(index + 1)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(model?.rows ?? []).map((row, rowIndex) => (
              <tr key={rowIndex}>
                <th className="sticky left-0 z-10 border border-[#e5e7eb] bg-[#f3f4f6] px-1.5 py-0.5 text-right font-normal text-[#666]">
                  {rowIndex + 1}
                </th>
                {row.map((cell, cellIndex) => (
                  <td
                    className="max-w-64 border border-[#e5e7eb] px-1.5 py-0.5 align-top whitespace-pre-wrap"
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
          <p className="px-2 py-1 text-[0.62rem] text-[#666]">{labels.truncated(SHEET_MAX_ROWS, SHEET_MAX_COLS)}</p>
        ) : null}
        {legacy ? <p className="px-2 py-1 text-[0.62rem] text-[#666]">{labels.legacyNote}</p> : null}
      </div>

      <PreviewStatus error={error} labels={labels} loading={loading} neutral />
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
    const ext = path.split('.').pop()?.toLowerCase()

    return ext === 'xlsx' ? (
      <Suspense
        fallback={
          <div className="flex min-h-0 flex-1 items-center justify-center bg-white">
            <PageLoader label={labels.loading} />
          </div>
        }
      >
        <BetterSheetEditor labels={labels} path={path} />
      </Suspense>
    ) : (
      <SheetPreview labels={labels} path={path} />
    )
  }

  if (kind === 'pdf') {
    return <PdfPreview labels={labels} path={path} />
  }

  if (kind === 'docx') {
    return <DocxPreview labels={labels} path={path} />
  }

  return null
}
