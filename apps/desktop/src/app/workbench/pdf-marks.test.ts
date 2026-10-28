import { describe, expect, it } from 'vitest'
import { PDFDocument } from 'pdf-lib'

import { bakePdfMarks, cssRectToPdf, hexToRgb, markColor } from './pdf-marks'

describe('pdf marks', () => {
  it('converts viewer pixels into pdf user space', () => {
    const rect = cssRectToPdf({ height: 20, width: 100, x: 50, y: 30 }, 2, 800)

    expect(rect).toEqual({ height: 10, width: 50, x: 25, y: 775 })
  })

  it('reads hex colours', () => {
    const color = hexToRgb('#fde68a')

    expect(color.r).toBeCloseTo(0.99, 2)
    expect(color.g).toBeCloseTo(0.9, 2)
    expect(color.b).toBeCloseTo(0.54, 2)
  })

  it('writes marks into the file and keeps it readable', async () => {
    const doc = await PDFDocument.create()
    const page = doc.addPage([600, 800])
    page.drawText('hello', { size: 12, x: 50, y: 700 })
    const original = await doc.save()

    const baked = await bakePdfMarks(original, [
      {
        color: markColor('highlight'),
        id: 'mark-1',
        kind: 'highlight',
        pageIndex: 0,
        rects: [{ height: 20, width: 100, x: 50, y: 30 }],
        scale: 2
      }
    ])

    expect(baked.byteLength).toBeGreaterThan(original.byteLength)

    const reloaded = await PDFDocument.load(baked)

    expect(reloaded.getPageCount()).toBe(1)
  })
})
