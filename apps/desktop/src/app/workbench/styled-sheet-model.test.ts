import ExcelJS from 'exceljs'
import { describe, expect, it } from 'vitest'

import { readStyledSheetModel } from './styled-sheet-model'

async function styledWorkbook(): Promise<Uint8Array> {
  const workbook = new ExcelJS.Workbook()
  const sheet = workbook.addWorksheet('S1')

  sheet.getCell('A1').value = 'Title'
  sheet.getCell('A1').fill = { fgColor: { argb: 'FFFFFF00' }, pattern: 'solid', type: 'pattern' }
  sheet.getCell('A1').font = { bold: true, color: { argb: 'FF990000' } }
  sheet.mergeCells('A1:B1')
  sheet.getCell('A2').value = 'left'
  sheet.getCell('B2').value = 2

  return new Uint8Array((await workbook.xlsx.writeBuffer()) as ArrayBuffer)
}

describe('readStyledSheetModel', () => {
  it('keeps fills, fonts and merged ranges', async () => {
    const model = await readStyledSheetModel(await styledWorkbook())

    expect(model.names).toEqual(['S1'])
    expect(model.rows[0]![0]).toMatchObject({
      colspan: 2,
      style: { background: '#ffff00', bold: true, color: '#990000' },
      text: 'Title'
    })
    expect(model.rows[0]![1]!.hidden).toBe(true)
    expect(model.rows[1]![0]!.text).toBe('left')
  })
})
