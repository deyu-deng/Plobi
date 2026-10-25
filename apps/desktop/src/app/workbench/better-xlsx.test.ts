import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'

import { normalizeXlsxColumnWidths } from './better-xlsx'

async function makeWorkbook(sheetXml: string): Promise<Uint8Array> {
  const zip = new JSZip()
  zip.file('xl/worksheets/sheet1.xml', sheetXml)
  zip.file('xl/workbook.xml', '<workbook/>')

  return zip.generateAsync({ type: 'uint8array' })
}

describe('normalizeXlsxColumnWidths', () => {
  it('clamps widths above Excel’s limit and leaves valid widths alone', async () => {
    const bytes = await makeWorkbook(
      '<worksheet><cols><col min="1" max="1" width="300.5" customWidth="1"/><col min="2" max="2" width="20" customWidth="1"/></cols></worksheet>'
    )

    const normalized = await normalizeXlsxColumnWidths(bytes)
    const zip = await JSZip.loadAsync(normalized)
    const xml = await zip.file('xl/worksheets/sheet1.xml')!.async('string')

    expect(xml).toContain('width="255"')
    expect(xml).toContain('width="20"')
  })

  it('returns the original bytes when every width is valid', async () => {
    const bytes = await makeWorkbook(
      '<worksheet><cols><col min="1" max="1" width="40" customWidth="1"/></cols></worksheet>'
    )

    const normalized = await normalizeXlsxColumnWidths(bytes)

    expect(normalized).toBe(bytes)
  })
})
