import Papa from 'papaparse'

export type DelimitedKind = 'csv' | 'tsv'

export interface DelimitedDialect {
  bom: boolean
  delimiter: string
  newline: '\n' | '\r\n'
  trailingNewline: boolean
}

export interface ParsedDelimited {
  columnCount: number
  dialect: DelimitedDialect
  rowLengths: number[]
  rows: string[][]
  tooLarge: boolean
}

export const DELIMITED_MAX_COLUMNS = 200
export const DELIMITED_MAX_ROWS = 5000

const DELIMITER_CANDIDATES = [',', ';', '\t', '|']

export function delimitedKind(path: string): DelimitedKind | null {
  const clean = path.split(/[?#]/u)[0]?.toLowerCase() ?? ''

  if (clean.endsWith('.tsv')) {
    return 'tsv'
  }

  if (clean.endsWith('.csv')) {
    return 'csv'
  }

  return null
}

function detectDelimiter(body: string): string {
  const sample = body.split(/\r?\n/u).slice(0, 10).join('\n')
  let best = ','
  let bestScore = 0

  for (const candidate of DELIMITER_CANDIDATES) {
    const parsed = Papa.parse<string[]>(sample, { delimiter: candidate, skipEmptyLines: true })
    const widths = parsed.data.map(row => row.length).filter(width => width > 1)

    if (widths.length === 0) {
      continue
    }

    const consistent = widths.every(width => width === widths[0])
    const score = widths[0] * (consistent ? 10 : 1) + widths.length

    if (score > bestScore) {
      best = candidate
      bestScore = score
    }
  }

  return best
}

export function parseDelimited(text: string, kind: DelimitedKind): ParsedDelimited {
  const bom = text.charCodeAt(0) === 0xfeff
  const body = bom ? text.slice(1) : text
  const newline: '\n' | '\r\n' = body.includes('\r\n') ? '\r\n' : '\n'
  const trailingNewline = /\r?\n$/u.test(body)
  const delimiter = kind === 'tsv' ? '\t' : detectDelimiter(body)

  if (!body.trim()) {
    return {
      columnCount: 1,
      dialect: { bom, delimiter, newline, trailingNewline },
      rowLengths: [],
      rows: [],
      tooLarge: false
    }
  }

  const parsed = Papa.parse<string[]>(body, {
    delimiter,
    newline,
    skipEmptyLines: false
  })

  const rows = parsed.data.map(row => row.map(cell => (cell == null ? '' : String(cell))))

  if (trailingNewline && rows.length > 0) {
    const last = rows[rows.length - 1]

    if (last.length === 1 && last[0] === '') {
      rows.pop()
    }
  }

  const rowLengths = rows.map(row => row.length)
  const columnCount = Math.max(1, ...rowLengths)

  return {
    columnCount,
    dialect: { bom, delimiter, newline, trailingNewline },
    rowLengths,
    rows,
    tooLarge: rows.length > DELIMITED_MAX_ROWS || columnCount > DELIMITED_MAX_COLUMNS
  }
}

export function serializeDelimited(
  rows: string[][],
  rowLengths: number[],
  dialect: DelimitedDialect
): string {
  const trimmed = rows.map((row, index) => {
    let end = row.length

    while (end > 0 && row[end - 1] === '') {
      end -= 1
    }

    const keep = Math.max(rowLengths[index] ?? 0, end)

    return row.slice(0, keep)
  })

  let text = Papa.unparse(trimmed, {
    delimiter: dialect.delimiter,
    newline: dialect.newline,
    quotes: false
  })

  if (dialect.trailingNewline && text && !text.endsWith(dialect.newline)) {
    text += dialect.newline
  }

  return dialect.bom ? `\uFEFF${text}` : text
}

export function delimitedColumnLabel(index: number): string {
  let label = ''
  let value = index

  do {
    label = String.fromCharCode(65 + (value % 26)) + label
    value = Math.floor(value / 26) - 1
  } while (value >= 0)

  return label
}
