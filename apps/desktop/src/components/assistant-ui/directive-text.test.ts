import { describe, expect, it } from 'vitest'

import { formatRefValue, plobiDirectiveFormatter } from './directive-text'

describe('formatRefValue', () => {
  it('leaves simple paths untouched', () => {
    expect(formatRefValue('src/index.ts')).toBe('src/index.ts')
    expect(formatRefValue('https://example.com/post')).toBe('https://example.com/post')
  })

  it('wraps paths with whitespace in backticks', () => {
    expect(formatRefValue('apple-touch-icon (1).png')).toBe('`apple-touch-icon (1).png`')
  })

  it('falls back to double quotes when value contains backticks', () => {
    expect(formatRefValue('weird `name` (1).md')).toBe('"weird `name` (1).md"')
  })
})

describe('plobiDirectiveFormatter.parse', () => {
  it('keeps quoted file paths whole when parsing', () => {
    const segments = plobiDirectiveFormatter.parse('see @image:`apple-touch-icon (1).png` for the icon')

    expect(segments).toEqual([
      { kind: 'text', text: 'see ' },
      { kind: 'mention', type: 'image', label: 'apple-touch-icon (1).png', id: 'apple-touch-icon (1).png' },
      { kind: 'text', text: ' for the icon' }
    ])
  })

  it('still parses unquoted paths', () => {
    const segments = plobiDirectiveFormatter.parse('@file:src/main.tsx the entry point')

    expect(segments).toEqual([
      { kind: 'mention', type: 'file', label: 'main.tsx', id: 'src/main.tsx' },
      { kind: 'text', text: ' the entry point' }
    ])
  })

  it('parses document-location refs (WP-OFFICE-SELECTION-TOOLBAR)', () => {
    expect(plobiDirectiveFormatter.parse('@page:notes/book.pdf:12')).toEqual([
      { kind: 'mention', type: 'page', label: 'book.pdf:12', id: 'notes/book.pdf:12' }
    ])
    expect(plobiDirectiveFormatter.parse('@section:docs/report.docx:3.2')).toEqual([
      { kind: 'mention', type: 'section', label: 'report.docx:3.2', id: 'docs/report.docx:3.2' }
    ])
  })
})
