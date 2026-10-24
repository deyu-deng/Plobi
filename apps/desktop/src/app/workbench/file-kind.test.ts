import { describe, expect, it } from 'vitest'

import { fileKindForPath } from './file-kind'

describe('fileKindForPath', () => {
  it('routes the formats WorkBuddy previews locally', () => {
    expect(fileKindForPath('a.xlsx')).toBe('sheet')
    expect(fileKindForPath('a.XLS')).toBe('sheet')
    expect(fileKindForPath('a.csv')).toBe('sheet')
    expect(fileKindForPath('a.pdf')).toBe('pdf')
    expect(fileKindForPath('a.docx')).toBe('docx')
    expect(fileKindForPath('a.PDF?raw=1')).toBe('pdf')
    expect(fileKindForPath('a.md')).toBe('other')
    expect(fileKindForPath('no-extension')).toBe('other')
  })
})
