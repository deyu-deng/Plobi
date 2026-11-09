import { describe, expect, it } from 'vitest'

import {
  DELIMITED_MAX_COLUMNS,
  DELIMITED_MAX_ROWS,
  delimitedColumnLabel,
  delimitedKind,
  parseDelimited,
  serializeDelimited
} from './delimited'

function roundTrip(text: string, kind: 'csv' | 'tsv' = 'csv') {
  const parsed = parseDelimited(text, kind)

  return serializeDelimited(parsed.rows, parsed.rowLengths, parsed.dialect)
}

describe('delimited', () => {
  it('recognises csv and tsv extensions', () => {
    expect(delimitedKind('D:/data/sheet.csv')).toBe('csv')
    expect(delimitedKind('D:/data/sheet.TSV')).toBe('tsv')
    expect(delimitedKind('D:/data/sheet.csv?x=1')).toBe('csv')
    expect(delimitedKind('D:/data/sheet.txt')).toBeNull()
  })

  it('round-trips simple csv with CRLF, BOM and trailing newline', () => {
    const text = '\uFEFFa,b\r\n1,2\r\n'
    const parsed = parseDelimited(text, 'csv')

    expect(parsed.rows).toEqual([
      ['a', 'b'],
      ['1', '2']
    ])
    expect(parsed.columnCount).toBe(2)
    expect(parsed.dialect).toEqual({ bom: true, delimiter: ',', newline: '\r\n', trailingNewline: true })
    expect(roundTrip(text)).toBe(text)
  })

  it('preserves quoted commas, quotes and newlines', () => {
    const text = 'a,"b,c"\r\n"say ""hi""",2\r\n'
    const parsed = parseDelimited(text, 'csv')

    expect(parsed.rows).toEqual([
      ['a', 'b,c'],
      ['say "hi"', '2']
    ])
    expect(roundTrip(text)).toBe(text)
  })

  it('detects semicolons and pipes', () => {
    expect(parseDelimited('a;b;c\n1;2;3\n', 'csv').dialect.delimiter).toBe(';')
    expect(parseDelimited('a|b\n1|2\n', 'csv').dialect.delimiter).toBe('|')
  })

  it('always uses tabs for tsv', () => {
    const text = 'a\tb\n1\t2\n'
    const parsed = parseDelimited(text, 'tsv')

    expect(parsed.dialect.delimiter).toBe('\t')
    expect(parsed.rows).toEqual([
      ['a', 'b'],
      ['1', '2']
    ])
    expect(roundTrip(text, 'tsv')).toBe(text)
  })

  it('keeps trailing empty cells that were in the source', () => {
    const text = 'a,b,\n1,2,\n'
    const parsed = parseDelimited(text, 'csv')

    expect(parsed.rowLengths).toEqual([3, 3])
    expect(roundTrip(text)).toBe(text)
  })

  it('handles empty input', () => {
    const parsed = parseDelimited('', 'csv')

    expect(parsed.rows).toEqual([])
    expect(serializeDelimited(parsed.rows, parsed.rowLengths, parsed.dialect)).toBe('')
  })

  it('flags oversized grids', () => {
    const manyRows = `a\n${'1\n'.repeat(DELIMITED_MAX_ROWS + 1)}`
    const wideRow = `${Array.from({ length: DELIMITED_MAX_COLUMNS + 1 }, (_, index) => `c${index}`).join(',')}\n`

    expect(parseDelimited(manyRows, 'csv').tooLarge).toBe(true)
    expect(parseDelimited(wideRow, 'csv').tooLarge).toBe(true)
    expect(parseDelimited('a,b\n1,2\n', 'csv').tooLarge).toBe(false)
  })

  it('labels columns like a spreadsheet', () => {
    expect(delimitedColumnLabel(0)).toBe('A')
    expect(delimitedColumnLabel(25)).toBe('Z')
    expect(delimitedColumnLabel(26)).toBe('AA')
    expect(delimitedColumnLabel(701)).toBe('ZZ')
    expect(delimitedColumnLabel(702)).toBe('AAA')
  })
})
