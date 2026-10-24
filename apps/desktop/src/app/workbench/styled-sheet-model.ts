import ExcelJS from 'exceljs'

import { SHEET_MAX_COLS, SHEET_MAX_ROWS } from './sheet-model'

export interface StyledSheetCell {
  text: string
  colspan: number
  rowspan: number
  hidden: boolean
  style: {
    background?: string
    color?: string
    bold?: boolean
    italic?: boolean
    fontSize?: number
    align?: 'center' | 'left' | 'right'
    valign?: 'bottom' | 'middle' | 'top'
    wrap?: boolean
    border: {
      top?: string
      right?: string
      bottom?: string
      left?: string
    }
  }
}

export interface StyledSheetModel {
  names: string[]
  rows: StyledSheetCell[][]
  widths: number[]
  truncated: boolean
}

function colorOf(color: Partial<ExcelJS.Color> | undefined): string | undefined {
  if (!color) {
    return undefined
  }

  const argb = color.argb

  if (!argb || argb.length < 6) {
    return undefined
  }

  return `#${argb.slice(-6).toLowerCase()}`
}

const BORDER_STYLE: Record<string, string> = {
  dashed: 'dashed',
  dotted: 'dotted',
  double: 'double',
  hair: 'solid',
  medium: 'solid',
  mediumDashed: 'dashed',
  mediumDashDot: 'dashed',
  mediumDashDotDot: 'dashed',
  slantDashDot: 'dashed',
  thick: 'solid',
  thin: 'solid'
}

const BORDER_WIDTH: Record<string, string> = {
  double: '3px',
  hair: '1px',
  medium: '2px',
  thick: '3px',
  thin: '1px'
}

function borderOf(side: Partial<ExcelJS.Border> | undefined): string | undefined {
  if (!side?.style) {
    return undefined
  }

  const style = BORDER_STYLE[side.style] ?? 'solid'
  const width = BORDER_WIDTH[side.style] ?? '1px'
  const color = colorOf(side.color) ?? '#9ca3af'

  return `${width} ${style} ${color}`
}

function decodeAddress(address: string): { col: number; row: number } | null {
  const match = /^([A-Z]+)(\d+)$/iu.exec(address)

  if (!match) {
    return null
  }

  let col = 0

  for (const char of match[1]!.toUpperCase()) {
    col = col * 26 + char.charCodeAt(0) - 64
  }

  return { col, row: Number(match[2]) }
}

function parseRange(range: string): { endCol: number; endRow: number; startCol: number; startRow: number } | null {
  const [startAddress, endAddress] = range.split(':')
  const start = startAddress ? decodeAddress(startAddress) : null
  const end = endAddress ? decodeAddress(endAddress) : null

  if (!start || !end) {
    return null
  }

  return { endCol: end.col, endRow: end.row, startCol: start.col, startRow: start.row }
}

function cellStyle(cell: ExcelJS.Cell): StyledSheetCell['style'] {
  const fill = cell.fill
  const background =
    fill && fill.type === 'pattern' && fill.pattern === 'solid' && 'fgColor' in fill
      ? colorOf(fill.fgColor)
      : undefined
  const alignment = cell.alignment
  const border = cell.border
  const horizontal = alignment?.horizontal
  const vertical = alignment?.vertical

  return {
    align:
      horizontal === 'center' || horizontal === 'right' || horizontal === 'left' ? horizontal : undefined,
    background,
    bold: Boolean(cell.font?.bold),
    border: {
      bottom: borderOf(border?.bottom),
      left: borderOf(border?.left),
      right: borderOf(border?.right),
      top: borderOf(border?.top)
    },
    color: colorOf(cell.font?.color),
    fontSize: typeof cell.font?.size === 'number' ? cell.font.size : undefined,
    italic: Boolean(cell.font?.italic),
    valign: vertical === 'middle' || vertical === 'top' || vertical === 'bottom' ? vertical : undefined,
    wrap: Boolean(alignment?.wrapText)
  }
}

export async function readStyledSheetModel(
  bytes: Uint8Array,
  sheetName?: string
): Promise<StyledSheetModel> {
  const workbook = new ExcelJS.Workbook()
  await workbook.xlsx.load(bytes as unknown as Parameters<typeof workbook.xlsx.load>[0])
  const names = workbook.worksheets.map(worksheet => worksheet.name)
  const worksheet = sheetName
    ? workbook.getWorksheet(sheetName) ?? workbook.worksheets[0]
    : workbook.worksheets[0]

  if (!worksheet) {
    return { names, rows: [], truncated: false, widths: [] }
  }

  const rowCount = Math.min(worksheet.rowCount, SHEET_MAX_ROWS)
  const colCount = Math.min(worksheet.columnCount, SHEET_MAX_COLS)
  const rows: StyledSheetCell[][] = []
  const spans = new Map<string, { colspan: number; rowspan: number }>()
  const covered = new Set<string>()

  for (const range of worksheet.model?.merges ?? []) {
    const parsed = parseRange(range)

    if (!parsed) {
      continue
    }

    const startRow = Math.min(parsed.startRow, rowCount)
    const startCol = Math.min(parsed.startCol, colCount)
    const endRow = Math.min(parsed.endRow, rowCount)
    const endCol = Math.min(parsed.endCol, colCount)

    if (startRow < 1 || startCol < 1 || endRow < startRow || endCol < startCol) {
      continue
    }

    spans.set(`${startRow}:${startCol}`, { colspan: endCol - startCol + 1, rowspan: endRow - startRow + 1 })

    for (let row = startRow; row <= endRow; row += 1) {
      for (let col = startCol; col <= endCol; col += 1) {
        if (row !== startRow || col !== startCol) {
          covered.add(`${row}:${col}`)
        }
      }
    }
  }

  for (let row = 1; row <= rowCount; row += 1) {
    const cells: StyledSheetCell[] = []

    for (let col = 1; col <= colCount; col += 1) {
      const key = `${row}:${col}`
      const cell = worksheet.getCell(row, col)
      const span = spans.get(key)

      cells.push({
        colspan: span?.colspan ?? 1,
        hidden: covered.has(key),
        rowspan: span?.rowspan ?? 1,
        style: cellStyle(cell),
        text: cell.text ?? ''
      })
    }

    rows.push(cells)
  }

  const widths = Array.from({ length: colCount }, (_, index) => {
    const width = worksheet.getColumn(index + 1).width ?? 10

    return Math.max(48, Math.min(320, Math.round(width * 7.5 + 8)))
  })

  return {
    names,
    rows,
    truncated: worksheet.rowCount > SHEET_MAX_ROWS || worksheet.columnCount > SHEET_MAX_COLS,
    widths
  }
}
