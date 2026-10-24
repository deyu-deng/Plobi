import * as XLSX from 'xlsx'

export const SHEET_MAX_ROWS = 200
export const SHEET_MAX_COLS = 60

export interface SheetModel {
  names: string[]
  rows: string[][]
  truncated: boolean
}

export function readSheetModel(bytes: Uint8Array, sheetName?: string): SheetModel {
  const workbook = XLSX.read(bytes, { type: 'array' })
  const names = workbook.SheetNames
  const activeName = sheetName && names.includes(sheetName) ? sheetName : names[0]

  if (!activeName) {
    return { names, rows: [], truncated: false }
  }

  const sheet = workbook.Sheets[activeName]
  const matrix = sheet
    ? (XLSX.utils.sheet_to_json(sheet, { defval: '', header: 1, raw: false }) as unknown[][])
    : []
  const truncated = matrix.length > SHEET_MAX_ROWS || matrix.some(row => row.length > SHEET_MAX_COLS)
  const rows = matrix.slice(0, SHEET_MAX_ROWS).map(row =>
    row.slice(0, SHEET_MAX_COLS).map(value => String(value ?? ''))
  )

  return { names, rows, truncated }
}
