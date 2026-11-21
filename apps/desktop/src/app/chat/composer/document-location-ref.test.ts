import { describe, expect, it } from 'vitest'

import { documentLocationRef } from './inline-refs'

/**
 * 文档定位引用（WP-OFFICE-SELECTION-TOOLBAR）：
 * PDF 出 `@page:`、Word/PPT 出 `@section:`。与 `@line:` 一样是纯文本 chip。
 */
describe('documentLocationRef', () => {
  it('builds a page ref with the page number', () => {
    expect(documentLocationRef('page', 'D:/notes/book.pdf', 'D:/notes', 12)).toBe('@page:book.pdf:12')
  })

  it('builds a section ref with a dotted section path', () => {
    expect(documentLocationRef('section', 'D:/docs/report.docx', 'D:/docs', '3.2')).toBe(
      '@section:report.docx:3.2'
    )
  })

  it('keeps the full path when the file is outside cwd', () => {
    expect(documentLocationRef('page', 'D:/elsewhere/book.pdf', 'D:/notes', 5)).toBe(
      '@page:D:/elsewhere/book.pdf:5'
    )
  })

  it('quotes a value containing spaces', () => {
    expect(documentLocationRef('page', 'D:/notes/book (1).pdf', 'D:/notes', 2)).toBe(
      '@page:`book (1).pdf:2`'
    )
  })

  it('still builds a ref when the page is unknown', () => {
    expect(documentLocationRef('page', 'D:/notes/book.pdf', 'D:/notes', '?')).toBe('@page:book.pdf:?')
  })
})
