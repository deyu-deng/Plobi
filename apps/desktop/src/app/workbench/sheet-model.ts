import * as XLSX from 'xlsx'

export const SHEET_MAX_ROWS = 200
export const SHEET_MAX_COLS = 60

// XLSX.read 是同步解析，整份灌进渲染进程会把主线程钉住；超过这个体积就不解析，
// 让用户自己决定要不要用系统 Office 打开。
export const SHEET_MAX_BYTES = 30 * 1024 * 1024

export interface SheetModel {
  names: string[]
  rows: string[][]
  truncated: boolean
  tooLarge: boolean
}

export function readSheetModel(bytes: Uint8Array, sheetName?: string): SheetModel {
  if (bytes.byteLength > SHEET_MAX_BYTES) {
    return { names: [], rows: [], truncated: false, tooLarge: true }
  }

  const workbook = XLSX.read(bytes, { type: 'array' })
  const names = workbook.SheetNames
  const activeName = sheetName && names.includes(sheetName) ? sheetName : names[0]

  if (!activeName) {
    return { names, rows: [], truncated: false, tooLarge: false }
  }

  const sheet = workbook.Sheets[activeName]
  const matrix = sheet
    ? (XLSX.utils.sheet_to_json(sheet, { defval: '', header: 1, raw: false }) as unknown[][])
    : []
  const truncated = matrix.length > SHEET_MAX_ROWS || matrix.some(row => row.length > SHEET_MAX_COLS)
  const rows = matrix.slice(0, SHEET_MAX_ROWS).map(row =>
    row.slice(0, SHEET_MAX_COLS).map(value => String(value ?? ''))
  )

  return { names, rows, truncated, tooLarge: false }
}
