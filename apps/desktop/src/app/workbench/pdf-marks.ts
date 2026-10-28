import { PDFDocument, rgb } from 'pdf-lib'

export interface PdfMarkRect {
  height: number
  width: number
  x: number
  y: number
}

export interface PdfMark {
  color: string
  id: string
  kind: 'highlight' | 'note'
  pageIndex: number
  rects: PdfMarkRect[]
  scale: number
  text?: string
}

const HIGHLIGHT_COLOR = '#fde68a'
const NOTE_COLOR = '#fbbf24'

export function markId() {
  return `mark-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function hexToRgb(hex: string) {
  const value = hex.replace('#', '')
  const full = value.length === 3 ? value.split('').map(part => part + part).join('') : value
  const int = Number.parseInt(full, 16)

  return {
    b: (int & 255) / 255,
    g: ((int >> 8) & 255) / 255,
    r: ((int >> 16) & 255) / 255
  }
}

/**
 * The viewer draws pages in CSS pixels (origin top-left) while PDF user space
 * has its origin bottom-left in points. Marks are stored in CSS pixels and
 * converted here, so the same numbers can drive both the overlay and the bake.
 */
export function cssRectToPdf(rect: PdfMarkRect, scale: number, pageHeightPoints: number) {
  return {
    height: rect.height / scale,
    width: rect.width / scale,
    x: rect.x / scale,
    y: pageHeightPoints - (rect.y + rect.height) / scale
  }
}

export async function bakePdfMarks(original: Uint8Array, marks: PdfMark[]): Promise<Uint8Array> {
  const doc = await PDFDocument.load(original)

  for (const mark of marks) {
    const page = doc.getPage(mark.pageIndex)
    const { height: pageHeight } = page.getSize()
    const color = hexToRgb(mark.color || HIGHLIGHT_COLOR)

    for (const rect of mark.rects) {
      const target = cssRectToPdf(rect, mark.scale, pageHeight)

      page.drawRectangle({
        color: rgb(color.r, color.g, color.b),
        height: target.height,
        opacity: mark.kind === 'note' ? 0.95 : 0.4,
        width: target.width,
        x: target.x,
        y: target.y
      })
    }

    if (mark.kind === 'note' && mark.text) {
      const first = mark.rects[0]

      if (first) {
        const target = cssRectToPdf(first, mark.scale, pageHeight)

        page.drawText(mark.text.slice(0, 120), {
          color: rgb(0.1, 0.1, 0.1),
          size: 9,
          x: target.x + 2,
          y: target.y + target.height - 10
        })
      }
    }
  }

  return doc.save()
}

export function markColor(kind: PdfMark['kind']) {
  return kind === 'note' ? NOTE_COLOR : HIGHLIGHT_COLOR
}
