import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'

import { readSheetModel, SHEET_MAX_ROWS } from './sheet-model'

function workbookBytes(rows: unknown[][]): Uint8Array {
  const workbook = XLSX.utils.book_new()
  const sheet = XLSX.utils.aoa_to_sheet(rows)
  XLSX.utils.book_append_sheet(workbook, sheet, 'S1')

  return new Uint8Array(XLSX.write(workbook, { bookType: 'xlsx', type: 'array' }))
}

describe('readSheetModel', () => {
  it('reads sheet names and cell text', () => {
    const model = readSheetModel(workbookBytes([['name', 'value'], ['alpha', 1]]))

    expect(model.names).toEqual(['S1'])
    expect(model.rows[0]).toEqual(['name', 'value'])
    expect(model.rows[1]).toEqual(['alpha', '1'])
    expect(model.truncated).toBe(false)
  })

  it('caps very large sheets and reports truncation', () => {
    const rows = Array.from({ length: SHEET_MAX_ROWS + 1 }, (_, index) => [String(index)])
    const model = readSheetModel(workbookBytes(rows))

    expect(model.rows).toHaveLength(SHEET_MAX_ROWS)
    expect(model.truncated).toBe(true)
  })
})
