import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

import { requestComposerFocus, requestComposerInsertRefs } from '@/app/chat/composer/focus'
import { documentLocationRef } from '@/app/chat/composer/inline-refs'
import { SelectionToolbar, type SelectionToolbarAction } from '@/components/selection-toolbar/selection-toolbar'
import { writeClipboardText } from '@/components/ui/copy-button'
import { Codicon } from '@/components/ui/codicon'
import { convertDesktopLegacyOffice, writeDesktopFileBufferAtomic } from '@/lib/desktop-fs'
import { Copy, Search } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { legacyOfficeFormat, type LegacyOfficeFormat } from '@/lib/legacy-office'
import { useI18n } from '@/i18n'
import { $currentCwd } from '@/store/session'
import type { PreviewTarget } from '@/store/preview'

import { fileKindForPath } from './file-kind'
import { bakePdfMarks, markColor, markId, type PdfMark, type PdfMarkRect } from './pdf-marks'
import { GenOfficeFileEditor } from './genoffice-editor'
import { useFileBytes } from './file-bytes'
import { useFileRevision } from './use-file-revision'
import { readSheetModel, SHEET_MAX_BYTES, SHEET_MAX_COLS, SHEET_MAX_ROWS } from './sheet-model'
import { useOfficeScrollMemory } from './use-office-scroll-memory'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl


export interface OfficePreviewLabels {
  loading: string
  unavailable: string
  readOnly: string
  findPlaceholder: string
  copyAll: string
  copied: string
  openExternal: string
  archiveEmpty: string
  highlight: string
  note: string
  saveMarks: string
  restoreOriginal: string
  marksSaved: string
  notePlaceholder: string
  truncated: (rows: number, cols: number) => string
  tooLarge: (mb: number) => string
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
  hint: string
  diskChanged: string
  dismiss: string
  convertToXlsx: string
  converting: string
  convertFailed: string
  legacyTitle: string
  legacyBody: (sourceExt: string, targetExt: string) => string
  legacyConvertNote: string
  convertLegacy: (targetExt: string) => string
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

function SheetPreview({
  labels,
  onOpenFile,
  path
}: {
  labels: OfficePreviewLabels
  onOpenFile?: (target: PreviewTarget) => void
  path: string
}) {
  const { change } = useFileRevision(path)
  const { bytes, error, loading } = useFileBytes(path, change)
  const [sheetName, setSheetName] = useState('')
  const [converting, setConverting] = useState(false)
  const [convertError, setConvertError] = useState(false)
  const model = useMemo(() => (bytes ? readSheetModel(bytes, sheetName || undefined) : null), [bytes, sheetName])
  const selected = model && model.names.includes(sheetName) ? sheetName : (model?.names[0] ?? '')
  const legacy = /\.xls$/iu.test(path)
  // 表格按 [文件, 工作表] 记位置：同一个 xls 里不同 sheet 的阅读位置互不干扰。
  const sheetScrollRef = useOfficeScrollMemory({ kind: `sheet:${selected}`, path })

  const convertToXlsx = async () => {
    if (!bytes || converting) {
      return
    }

    setConverting(true)
    setConvertError(false)

    try {
      const result = await convertDesktopLegacyOffice(path)

      if (!result.ok || !result.path) {
        throw new Error(result.error || 'conversion failed')
      }

      onOpenFile?.({
        kind: 'file',
        label: result.path.split(/[\\/]/).pop() ?? result.path,
        path: result.path,
        source: 'file-browser',
        url: result.path
      })
    } catch {
      setConvertError(true)
    } finally {
      setConverting(false)
    }
  }

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
        {legacy ? (
          <button
            className="shrink-0 rounded border border-[#d1d5db] px-1.5 py-0.5 text-[0.6rem] font-medium text-[#333] transition-colors hover:bg-[#f3f4f6] disabled:opacity-50"
            disabled={!bytes || converting}
            onClick={() => void convertToXlsx()}
            type="button"
          >
            {converting ? labels.converting : labels.convertToXlsx}
          </button>
        ) : null}
        <span className={cn('shrink-0 text-[0.6rem]', convertError ? 'text-[#b91c1c]' : 'text-[#777]')}>
          {convertError ? labels.convertFailed : labels.readOnly}
        </span>
      </div>

      {model?.tooLarge ? (
        <div className="min-h-0 flex-1 overflow-auto bg-white">
          <p className="px-2 py-1 text-[0.62rem] text-[#666]">
            {labels.tooLarge(Math.round(SHEET_MAX_BYTES / (1024 * 1024)))}
          </p>
        </div>
      ) : (
      <div className="min-h-0 flex-1 overflow-auto bg-white" ref={sheetScrollRef}>
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

      )}

      <PreviewStatus error={error} labels={labels} loading={loading} neutral />
    </div>
  )
}

interface PdfTextNode {
  end: number
  node: Text
  start: number
}

type HighlightRegistryLike = {
  delete: (name: string) => void
  set: (name: string, highlight: unknown) => void
}

function pdfTextNodeAt(nodes: PdfTextNode[], offset: number) {
  for (const item of nodes) {
    if (offset >= item.start && offset <= item.end) {
      return { node: item.node, offset: Math.min(offset - item.start, item.node.data.length) }
    }
  }

  return null
}

const PDF_SCALE = 1.2

interface PdfRenderJob {
  canvas: HTMLCanvasElement
  page: pdfjsLib.PDFPageProxy
  pageNumber: number
  textLayer: HTMLDivElement
  // 页顶在滚动容器内容坐标系里的位置，骨架铺完量一次即可（页高由 PDF 尺寸定死，不随窗口变）。
  top: number
  viewport: ReturnType<pdfjsLib.PDFPageProxy['getViewport']>
}

function PdfPreview({ labels, path }: { labels: OfficePreviewLabels; path: string }) {
  const { change } = useFileRevision(path)
  const { bytes, error, loading } = useFileBytes(path, change)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const pageRefs = useRef(new Map<number, HTMLElement>())
  const sourceBytesRef = useRef<Uint8Array | null>(null)
  const textLayersRef = useRef<HTMLDivElement[]>([])
  // 虚拟化后视口外的页不会自动生成文本层，搜索与全文复制需要全文时由这个钩子把文字补齐。
  const ensureAllTextRef = useRef<null | (() => Promise<void>)>(null)
  const textNodesRef = useRef<PdfTextNode[]>([])
  const matchRangesRef = useRef<Range[]>([])
  const [query, setQuery] = useState('')
  const [matchCount, setMatchCount] = useState(0)
  const [activeMatch, setActiveMatch] = useState(0)
  const [textVersion, setTextVersion] = useState(0)
  const bakedRef = useRef(false)
  const [rendering, setRendering] = useState(false)
  const [renderError, setRenderError] = useState(false)
  const [marks, setMarks] = useState<PdfMark[]>([])
  const [noteMode, setNoteMode] = useState(false)
  const [noteDraft, setNoteDraft] = useState<{ pageIndex: number; x: number; y: number } | null>(null)
  const [noteText, setNoteText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)
  const { t } = useI18n()
  const pdfScrollRef = useOfficeScrollMemory({ kind: 'pdf', path })

  // 身份必须稳定：内联箭头每次渲染都换新身份，React 会先传 null 再传节点，
  // 位置恢复就会在每次 setState 后被重新触发。
  const mergedPdfRef = useCallback(
    (node: HTMLDivElement | null) => {
      hostRef.current = node
      pdfScrollRef(node)
    },
    [pdfScrollRef]
  )

  useEffect(() => {
    const host = hostRef.current

    if (!host || !bytes) {
      return
    }

    let cancelled = false
    let pdf: pdfjsLib.PDFDocumentProxy | null = null
    let detachViewport: (() => void) | null = null
    host.replaceChildren()
    pageRefs.current.clear()
    textLayersRef.current = []
    setRendering(true)
    setRenderError(false)
    setQuery('')
    setMatchCount(0)
    setActiveMatch(0)
    setMarks([])
    setNoteMode(false)
    setNoteDraft(null)

    if (!bakedRef.current) {
      // pdf.js transfers the buffer to its worker, which detaches ours — keep a
      // copy for the bake step.
      sourceBytesRef.current = bytes.slice()
    }
    void (async () => {
      try {
        pdf = await pdfjsLib.getDocument({ data: bytes }).promise

        if (cancelled) {
          return
        }

        // 第一段只问尺寸不画内容：页高一开始就定得下来，把每页的空白骨架一次铺满，
        // 滚动条总高度、跳页锚点、阅读位置坐标立刻作数，不必等任何一页画完。
        const sheets: PdfRenderJob[] = []

        for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
          if (cancelled) {
            break
          }

          const page = await pdf.getPage(pageNumber)
          const viewport = page.getViewport({ scale: PDF_SCALE })
          const wrapper = document.createElement('div')
          wrapper.className = 'plobi-pdf-page'
          wrapper.dataset.pdfPage = String(pageNumber)
          wrapper.style.width = `${viewport.width}px`
          wrapper.style.height = `${viewport.height}px`

          const canvas = document.createElement('canvas')
          canvas.className = 'h-full w-full'
          // 骨架期不分配位图：整页 canvas 位图一页就是几 MB，全量分配等于先把内存吃满。
          // 进入视口时再由 paint 按页设宽高，滚出去就归零。
          wrapper.append(canvas)

          const textLayer = document.createElement('div')
          textLayer.className = 'plobi-pdf-text'
          textLayer.style.height = `${viewport.height}px`
          textLayer.style.setProperty('--scale-factor', String(viewport.scale))
          textLayer.style.width = `${viewport.width}px`
          wrapper.append(textLayer)
          wrapper.addEventListener('click', event =>
            startNote(pageNumber, event as unknown as React.MouseEvent)
          )
          host.append(wrapper)
          pageRefs.current.set(pageNumber, wrapper)
          sheets.push({ canvas, page, pageNumber, textLayer, top: 0, viewport })
        }

        if (cancelled) {
          return
        }

        // 一次布局把每页的纵向位置量下来，可视判定全靠这张表，避免逐页再摸 DOM。
        const hostRect = host.getBoundingClientRect()
        const scrollTop = host.scrollTop

        for (const sheet of sheets) {
          const rect = sheet.textLayer.parentElement!.getBoundingClientRect()
          sheet.top = rect.top - hostRect.top + scrollTop
        }

        // 骨架齐了就撤加载态：用户先看到页面轮廓，后可滚动可跳页，绘制按视口来。
        setRendering(false)

        const canvasReady = new Set<number>()
        const textReady = new Set<number>()
        // 文本层按页收进 Map 再按页序导出：绘制顺序跟着视口走，直接 push 会让全文复制乱序。
        const textLayersByPage = new Map<number, HTMLDivElement>()
        const inflight = new Map<number, ReturnType<pdfjsLib.PDFPageProxy['render']>>()
        let targetPages = new Set<number>()
        let draining = false
        let textDirty = false
        let queued: PdfRenderJob[] = []

        // 文本层只要画一次就永久留着：搜索高亮、批注定位都靠它遍历文字，
        // 而它只有文字节点，不像 canvas 那样占位图内存。
        const paintTextLayer = async (job: PdfRenderJob) => {
          if (textReady.has(job.pageNumber)) {
            return
          }

          textReady.add(job.pageNumber)

          try {
            const layer = new pdfjsLib.TextLayer({
              container: job.textLayer,
              textContentSource: job.page.streamTextContent(),
              viewport: job.viewport
            })

            await layer.render()

            if (!cancelled) {
              textLayersByPage.set(job.pageNumber, job.textLayer)
              textLayersRef.current = [...textLayersByPage.entries()]
                .sort((left, right) => left[0] - right[0])
                .map(([, layerNode]) => layerNode)
              textDirty = true
            }
          } catch {
            // Scanned pages have no text layer; the canvas still previews.
          }
        }

        const paint = async (job: PdfRenderJob) => {
          job.canvas.width = Math.floor(job.viewport.width)
          job.canvas.height = Math.floor(job.viewport.height)

          const task = job.page.render({
            canvasContext: job.canvas.getContext('2d')!,
            viewport: job.viewport
          })

          inflight.set(job.pageNumber, task)

          try {
            await task.promise
          } catch {
            inflight.delete(job.pageNumber)

            return
          }

          inflight.delete(job.pageNumber)

          if (cancelled || !targetPages.has(job.pageNumber)) {
            return
          }

          canvasReady.add(job.pageNumber)
          await paintTextLayer(job)
        }

        const release = (job: PdfRenderJob) => {
          const task = inflight.get(job.pageNumber)

          if (task) {
            task.cancel()
            inflight.delete(job.pageNumber)
          }

          canvasReady.delete(job.pageNumber)

          // 位图归零才算真释放：只清像素内存不还，画一半被取消的页也一样挂着整页位图。
          if (job.canvas.width !== 0 || job.canvas.height !== 0) {
            job.canvas.width = 0
            job.canvas.height = 0
          }
        }

        const drain = async () => {
          if (draining) {
            return
          }

          draining = true

          try {
            while (queued.length > 0 && !cancelled) {
              const job = queued.shift()!

              if (!targetPages.has(job.pageNumber) || canvasReady.has(job.pageNumber)) {
                continue
              }

              await paint(job)
            }
          } finally {
            draining = false
          }

          if (textDirty && !cancelled) {
            textDirty = false
            setTextVersion(value => value + 1)
          }
        }

        const syncViewport = () => {
          if (cancelled) {
            return
          }

          const viewTop = host.scrollTop
          const viewBottom = viewTop + host.clientHeight
          const center = viewTop + host.clientHeight / 2
          const next = new Set<number>()
          const wanted: PdfRenderJob[] = []

          for (const job of sheets) {
            const jobTop = job.top
            const jobBottom = job.top + job.viewport.height

            // 上下各多留一页做缓冲，滚动时不至于刚露头就是白的。
            if (jobBottom >= viewTop - job.viewport.height && jobTop <= viewBottom + job.viewport.height) {
              next.add(job.pageNumber)
              wanted.push(job)
            }
          }

          targetPages = next

          // 容器高度还没量出来时视口范围是空的，先兜底画第一页，别让窗口开头一片白。
          if (next.size === 0) {
            const first = sheets[0]

            if (first) {
              next.add(first.pageNumber)
              wanted.push(first)
            }
          }

          for (const job of sheets) {
            if (!next.has(job.pageNumber)) {
              release(job)
            }
          }

          // 先画最靠近视线中心的那页，视觉上最快落定。
          wanted.sort(
            (left, right) =>
              Math.abs(left.top + left.viewport.height / 2 - center) -
              Math.abs(right.top + right.viewport.height / 2 - center)
          )
          queued = wanted.filter(job => !canvasReady.has(job.pageNumber))
          void drain()
        }

        // 搜索、全文复制要摸全文，视口外的页文本层也得补齐，否则拿不到没滚到的页。
        ensureAllTextRef.current = async () => {
          for (const job of sheets) {
            if (cancelled) {
              return
            }

            await paintTextLayer(job)
          }

          if (textDirty && !cancelled) {
            textDirty = false
            setTextVersion(value => value + 1)
          }
        }

        let frame = 0

        const scheduleSync = () => {
          if (frame) {
            return
          }

          frame = window.requestAnimationFrame(() => {
            frame = 0
            syncViewport()
          })
        }

        host.addEventListener('scroll', scheduleSync, { passive: true })
        window.addEventListener('resize', scheduleSync)
        // 视口高度变化（窗口缩放、面板拖拽）不会触发 scroll，得单独盯容器尺寸。
        const observer = new globalThis.ResizeObserver(scheduleSync)
        observer.observe(host)

        detachViewport = () => {
          host.removeEventListener('scroll', scheduleSync)
          window.removeEventListener('resize', scheduleSync)
          observer.disconnect()
          ensureAllTextRef.current = null

          if (frame) {
            window.cancelAnimationFrame(frame)
            frame = 0
          }

          for (const task of inflight.values()) {
            task.cancel()
          }

          inflight.clear()
        }

        syncViewport()
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
      detachViewport?.()
      host.replaceChildren()
      void pdf?.destroy()
    }
  }, [bytes])

  useEffect(() => {
    const registry = (CSS as unknown as { highlights?: HighlightRegistryLike }).highlights
    registry?.delete('plobi-pdf-search')
    registry?.delete('plobi-pdf-search-current')
    matchRangesRef.current = []
    setMatchCount(0)
    setActiveMatch(0)

    const needle = query.trim().toLocaleLowerCase()

    if (!needle) {
      return
    }

    // 视口外的页文本层还没生成时先补齐，补齐会推进 textVersion 让搜索重算一次。
    void ensureAllTextRef.current?.()

    const nodes: PdfTextNode[] = []
    let text = ''

    for (const layer of textLayersRef.current) {
      const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT)
      let current = walker.nextNode()

      while (current) {
        const node = current as Text

        if (node.data) {
          nodes.push({ end: text.length + node.data.length, node, start: text.length })
          text += node.data
        }

        current = walker.nextNode()
      }

      text += '\n'
    }

    textNodesRef.current = nodes
    const haystack = text.toLocaleLowerCase()
    const ranges: Range[] = []
    let at = haystack.indexOf(needle)

    while (at >= 0 && ranges.length < 300) {
      const start = pdfTextNodeAt(nodes, at)
      const end = pdfTextNodeAt(nodes, at + needle.length)

      if (start && end) {
        const range = document.createRange()
        range.setStart(start.node, start.offset)
        range.setEnd(end.node, end.offset)
        ranges.push(range)
      }

      at = haystack.indexOf(needle, at + needle.length)
    }

    matchRangesRef.current = ranges
    setMatchCount(ranges.length)
    setActiveMatch(0)
  }, [query, textVersion])

  useEffect(() => {
    const registry = (CSS as unknown as { highlights?: HighlightRegistryLike }).highlights
    const HighlightCtor = (window as unknown as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight

    if (!registry || !HighlightCtor) {
      return
    }

    const ranges = matchRangesRef.current
    registry.delete('plobi-pdf-search')
    registry.delete('plobi-pdf-search-current')

    if (!ranges.length) {
      return
    }

    registry.set('plobi-pdf-search', new HighlightCtor(...ranges))
    const current = ranges[Math.min(activeMatch, ranges.length - 1)]

    if (current) {
      registry.set('plobi-pdf-search-current', new HighlightCtor(current))
      current.startContainer.parentElement?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [activeMatch, matchCount])

  useEffect(() => {
    for (const [pageNumber, element] of pageRefs.current) {
      element.querySelectorAll('.plobi-pdf-mark').forEach(node => node.remove())

      for (const mark of marks.filter(item => item.pageIndex + 1 === pageNumber)) {
        for (const rect of mark.rects) {
          const box = document.createElement('div')
          box.className = 'plobi-pdf-mark'
          box.style.background = mark.color
          box.style.height = `${rect.height}px`
          box.style.left = `${rect.x}px`
          box.style.top = `${rect.y}px`
          box.style.width = `${rect.width}px`

          if (mark.text) {
            box.title = mark.text
          }

          element.append(box)
        }
      }
    }
  }, [marks])

  const stepMatch = (direction: number) => {
    if (!matchCount) {
      return
    }

    setActiveMatch(value => (value + direction + matchCount) % matchCount)
  }

  const addHighlight = useCallback(() => {
    const selection = window.getSelection()

    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return
    }

    const range = selection.getRangeAt(0)
    const groups = new Map<number, PdfMarkRect[]>()

    for (const clientRect of Array.from(range.getClientRects())) {
      if (clientRect.width < 1 || clientRect.height < 1) {
        continue
      }

      for (const [pageNumber, element] of pageRefs.current) {
        const pageRect = element.getBoundingClientRect()

        if (clientRect.bottom < pageRect.top || clientRect.top > pageRect.bottom) {
          continue
        }

        const list = groups.get(pageNumber) ?? []
        list.push({
          height: clientRect.height,
          width: clientRect.width,
          x: clientRect.left - pageRect.left,
          y: clientRect.top - pageRect.top
        })
        groups.set(pageNumber, list)
      }
    }

    if (!groups.size) {
      return
    }

    setMarks(previous => [
      ...previous,
      ...Array.from(groups.entries()).map(([pageIndex, rects]) => ({
        color: markColor('highlight'),
        id: markId(),
        kind: 'highlight' as const,
        pageIndex: pageIndex - 1,
        rects,
        scale: PDF_SCALE
      }))
    ])
    selection.removeAllRanges()
  }, [])

  // 选区浮窗（WP-OFFICE-SELECTION-TOOLBAR）：PDF 的文字层画在主进程 DOM 里，
  // window.getSelection() 够得到 ⇒ 直接复用那份共享浮窗，只是动作换成 PDF 这三枚。
  const pdfSelectionActions = useMemo<SelectionToolbarAction[]>(
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
        onSelect: ({ range }) => {
          // 从选区起点往上找到它所在的那一页（wrapper 带 data-pdf-page）。
          let node: Node | null = range.startContainer
          let page: string | null = null

          while (node) {
            if (node.nodeType === Node.ELEMENT_NODE) {
              const found = (node as HTMLElement).dataset?.pdfPage

              if (found) {
                page = found
                break
              }
            }
            node = node.parentNode
          }

          const ref = documentLocationRef('page', path, $currentCwd.get(), page ?? '?')
          requestComposerInsertRefs([ref])
          requestComposerFocus('main')
        }
      },
      {
        id: 'highlight',
        icon: <Codicon name="paintcan" size="0.85rem" />,
        label: labels.highlight,
        onSelect: () => {
          // addHighlight 自己读 window.getSelection()；浮窗的 mousedown 已经
          // preventDefault 保住了选区，这里照原样高亮即可。
          addHighlight()
        }
      }
    ],
    [addHighlight, labels.highlight, path, t]
  )

  const startNote = (pageIndex: number, event: React.MouseEvent) => {
    if (!noteMode) {
      return
    }

    const element = pageRefs.current.get(pageIndex)

    if (!element) {
      return
    }

    const rect = element.getBoundingClientRect()
    setNoteDraft({ pageIndex, x: event.clientX - rect.left, y: event.clientY - rect.top })
    setNoteText('')
  }

  const commitNote = () => {
    if (!noteDraft) {
      return
    }

    const text = noteText.trim()

    if (text) {
      setMarks(previous => [
        ...previous,
        {
          color: markColor('note'),
          id: markId(),
          kind: 'note',
          pageIndex: noteDraft.pageIndex - 1,
          rects: [{ height: 16, width: 16, x: noteDraft.x, y: noteDraft.y }],
          scale: PDF_SCALE,
          text
        }
      ])
    }

    setNoteDraft(null)
    setNoteText('')
    setNoteMode(false)
  }

  const writeMarks = async (payload: PdfMark[], restore: boolean) => {
    const source = sourceBytesRef.current

    if (!source) {
      return
    }

    setSaving(true)
    setSaveError(null)

    try {
      const baked = restore ? source : await bakePdfMarks(source, payload)
      await writeDesktopFileBufferAtomic(path, baked)
      bakedRef.current = !restore
      setMarks([])
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2500)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error))
    } finally {
      setSaving(false)
    }
  }

  const copyAll = async () => {
    // 虚拟化下只有视口附近的页画过文本层，复制全文前先把剩下的补齐。
    await ensureAllTextRef.current?.()

    const text = textLayersRef.current
      .map(layer => layer.innerText)
      .join('\n\n')
      .trim()

    if (!text) {
      return
    }

    let copiedOk = false

    try {
      copiedOk = (await window.plobiDesktop?.writeClipboard?.(text)) ?? false
    } catch {
      copiedOk = false
    }

    if (!copiedOk) {
      try {
        await navigator.clipboard.writeText(text)
        copiedOk = true
      } catch {
        copiedOk = false
      }
    }

    if (copiedOk) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <Search className="size-3 shrink-0 text-muted-foreground" />
          <input
            className="h-5 min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-transparent px-1.5 text-[0.64rem] outline-none placeholder:text-muted-foreground focus:border-(--ui-stroke-secondary)"
            onChange={event => setQuery(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                stepMatch(event.shiftKey ? -1 : 1)
              }

              if (event.key === 'Escape') {
                setQuery('')
              }
            }}
            placeholder={labels.findPlaceholder}
            value={query}
          />
          {query ? (
            <span className="shrink-0 text-[0.6rem] tabular-nums text-muted-foreground">
              {matchCount ? `${Math.min(activeMatch + 1, matchCount)}/${matchCount}` : '0/0'}
            </span>
          ) : null}
        </div>
        <button
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
          disabled={!textVersion}
          onClick={() => void copyAll()}
          title={copied ? labels.copied : labels.copyAll}
          type="button"
        >
          <Copy className="size-3" />
          <span>{copied ? labels.copied : labels.copyAll}</span>
        </button>
        <span className="shrink-0 text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>

      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2">
        <button
          className="flex items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onMouseDown={event => event.preventDefault()}
          onClick={addHighlight}
          title={labels.highlight}
          type="button"
        >
          <Codicon name="paintcan" size="0.72rem" />
          <span>{labels.highlight}</span>
        </button>
        <button
          className={cn(
            'flex items-center gap-1 rounded px-1 text-[0.6rem] transition-colors hover:bg-accent',
            noteMode ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground'
          )}
          onClick={() => {
            setNoteMode(value => !value)
            setNoteDraft(null)
          }}
          title={labels.note}
          type="button"
        >
          <Codicon name="comment" size="0.72rem" />
          <span>{labels.note}</span>
        </button>
        <button
          className="flex items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
          disabled={!marks.length || saving}
          onClick={() => void writeMarks(marks, false)}
          title={labels.saveMarks}
          type="button"
        >
          <Codicon name="save" size="0.72rem" />
          <span>{marks.length ? `${labels.saveMarks} (${marks.length})` : labels.saveMarks}</span>
        </button>
        {bakedRef.current ? (
          <button
            className="flex items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
            disabled={saving}
            onClick={() => void writeMarks([], true)}
            title={labels.restoreOriginal}
            type="button"
          >
            <Codicon name="discard" size="0.72rem" />
            <span>{labels.restoreOriginal}</span>
          </button>
        ) : null}
        {saved ? <span className="text-[0.6rem] text-emerald-600">{labels.marksSaved}</span> : null}
        {saveError ? <span className="truncate text-[0.6rem] text-destructive">{saveError}</span> : null}
      </div>

      <div
        className="min-h-0 flex-1 overflow-auto p-2"
        ref={mergedPdfRef}
      />
      <SelectionToolbar
        actions={pdfSelectionActions}
        containerRef={hostRef}
        testId="pdf-selection-toolbar"
      />
      <PreviewStatus error={error || renderError} labels={labels} loading={loading || rendering} />
      {noteDraft ? (
        <div
          className="absolute z-20"
          style={{ left: 12, top: 64 }}
        >
          <input
            autoFocus
            className="h-6 w-48 rounded border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-1.5 text-[0.66rem] outline-none"
            onBlur={commitNote}
            onChange={event => setNoteText(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commitNote()
              }

              if (event.key === 'Escape') {
                setNoteDraft(null)
                setNoteText('')
              }
            }}
            placeholder={labels.notePlaceholder}
            value={noteText}
          />
        </div>
      ) : null}
    </div>
  )
}

function LegacyOfficePreview({
  format,
  labels,
  onOpenFile,
  path
}: {
  format: LegacyOfficeFormat
  labels: OfficePreviewLabels
  onOpenFile?: (target: PreviewTarget) => void
  path: string
}) {
  const [converting, setConverting] = useState(false)
  const [convertError, setConvertError] = useState(false)
  const sourceExt = path.split(/[?#]/u)[0]?.split('.').pop()?.toUpperCase() ?? ''

  const convert = async () => {
    if (converting) {
      return
    }

    setConverting(true)
    setConvertError(false)

    try {
      const result = await convertDesktopLegacyOffice(path)

      if (!result.ok || !result.path) {
        throw new Error(result.error || 'conversion failed')
      }

      onOpenFile?.({
        kind: 'file',
        label: result.path.split(/[\\/]/).pop() ?? result.path,
        path: result.path,
        source: 'file-browser',
        url: result.path
      })
    } catch {
      setConvertError(true)
    } finally {
      setConverting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 bg-(--ui-sidebar-surface-background) px-6 text-center">
      <Codicon name="file" size="1.5rem" />
      <p className="text-[0.8rem] font-medium text-foreground">{labels.legacyTitle}</p>
      <p className="max-w-sm text-[0.68rem] leading-relaxed text-muted-foreground">
        {labels.legacyBody(sourceExt, format.targetExt)}
      </p>
      <button
        className="rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-3 py-1.5 text-[0.7rem] font-medium text-foreground transition-colors hover:bg-(--ui-row-hover-background) disabled:opacity-50"
        disabled={converting}
        onClick={() => void convert()}
        type="button"
      >
        {converting ? labels.converting : labels.convertLegacy(format.targetExt)}
      </button>
      {convertError ? (
        <p className="text-[0.66rem] text-destructive">{labels.convertFailed}</p>
      ) : (
        <p className="max-w-sm text-[0.62rem] leading-relaxed text-(--ui-text-tertiary)">
          {labels.legacyConvertNote}
        </p>
      )}
    </div>
  )
}

export function OfficeFilePreview({
  labels,
  onOpenFile,
  target
}: {
  labels: OfficePreviewLabels
  onOpenFile?: (target: PreviewTarget) => void
  target: PreviewTarget
}) {
  const path = target.path ?? ''
  const kind = fileKindForPath(path)
  const legacy = legacyOfficeFormat(path)

  if (kind === 'legacy' && legacy) {
    return <LegacyOfficePreview format={legacy} labels={labels} onOpenFile={onOpenFile} path={path} />
  }

  if (kind === 'sheet') {
    const ext = path.split('.').pop()?.toLowerCase()

    return ext === 'xlsx' ? (
      <GenOfficeFileEditor kind="sheets" labels={labels} path={path} />
    ) : (
      <SheetPreview labels={labels} onOpenFile={onOpenFile} path={path} />
    )
  }

  if (kind === 'pdf') {
    return <PdfPreview labels={labels} path={path} />
  }

  if (kind === 'docx') {
    return <GenOfficeFileEditor kind="word" labels={labels} path={path} />
  }

  if (kind === 'pptx') {
    return <GenOfficeFileEditor kind="slides" labels={labels} path={path} />
  }

  return null
}
