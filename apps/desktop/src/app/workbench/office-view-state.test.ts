import { describe, expect, it, vi } from 'vitest'

import {
  applyOfficeViewState,
  OFFICE_VIEW_STATE_KEY_PREFIX,
  officeViewStateKey,
  officeViewStateScript,
  parseOfficeViewSnapshot
} from './office-view-state'

describe('officeViewStateKey', () => {
  it('keeps kinds and paths from colliding', () => {
    expect(officeViewStateKey('word', '/a/report.docx')).toBe(`${OFFICE_VIEW_STATE_KEY_PREFIX}:word:/a/report.docx`)
    expect(officeViewStateKey('sheets', '/a/report.docx')).not.toBe(officeViewStateKey('word', '/a/report.docx'))
  })
})

describe('parseOfficeViewSnapshot', () => {
  it('reads a ratio that came back from JSON', () => {
    expect(parseOfficeViewSnapshot('{"at":1,"ratio":0.42}')).toEqual({ at: 1, ratio: 0.42 })
  })

  it('treats a missing timestamp as zero rather than dropping the position', () => {
    expect(parseOfficeViewSnapshot('{"ratio":0}')).toEqual({ at: 0, ratio: 0 })
  })

  it('rejects anything that would scroll somewhere impossible', () => {
    for (const raw of [
      '',
      'null',
      'undefined',
      '{"ratio":1.5}',
      '{"ratio":-0.1}',
      '{"ratio":"0.5"}',
      '{"ratio":null}',
      '{not json'
    ]) {
      expect(parseOfficeViewSnapshot(raw)).toBeNull()
    }
  })

  it('rejects non-string storage reads', () => {
    expect(parseOfficeViewSnapshot(null)).toBeNull()
    expect(parseOfficeViewSnapshot(undefined)).toBeNull()
    expect(parseOfficeViewSnapshot(0.5)).toBeNull()
  })
})

describe('officeViewStateScript', () => {
  it('carries the per-file key so two files never share a position', () => {
    const a = officeViewStateScript({ kind: 'word', path: '/a/one.docx' })
    const b = officeViewStateScript({ kind: 'word', path: '/a/two.docx' })

    expect(a).toContain(JSON.stringify(officeViewStateKey('word', '/a/one.docx')))
    expect(a).not.toBe(b)
  })

  it('is idempotent per file — re-injecting after a reload must not stack listeners', () => {
    const script = officeViewStateScript({ kind: 'sheets', path: '/a/book.xlsx' })

    expect(script).toContain('window.__plobiOfficeViewState === KEY')
    expect(script).toContain('window.__plobiOfficeViewState = KEY')
  })

  it('survives a path with quotes and backslashes', () => {
    const path = 'C:\\Users\\o"brien\\报告 v2.docx'

    expect(officeViewStateScript({ kind: 'word', path })).toContain(JSON.stringify(officeViewStateKey('word', path)))
  })
})

describe('applyOfficeViewState', () => {
  it('skips webviews that have no script runner (jsdom test doubles, early boot)', () => {
    expect(() => applyOfficeViewState(null, 'word', '/a/one.docx')).not.toThrow()
    expect(() => applyOfficeViewState({}, 'word', '/a/one.docx')).not.toThrow()
  })

  it('does nothing without a path', () => {
    const executeJavaScript = vi.fn()

    applyOfficeViewState({ executeJavaScript }, 'word', '')

    expect(executeJavaScript).not.toHaveBeenCalled()
  })

  it('injects the per-file script', () => {
    const executeJavaScript = vi.fn().mockResolvedValue(undefined)

    applyOfficeViewState({ executeJavaScript }, 'word', '/a/one.docx')

    expect(executeJavaScript).toHaveBeenCalledTimes(1)
    expect(executeJavaScript.mock.calls[0][0]).toContain(JSON.stringify(officeViewStateKey('word', '/a/one.docx')))
  })

  it('swallows a rejected injection so a dead webview cannot break the preview', async () => {
    const executeJavaScript = vi.fn().mockRejectedValue(new Error('gone'))

    expect(() => applyOfficeViewState({ executeJavaScript }, 'word', '/a/one.docx')).not.toThrow()

    await Promise.resolve()
  })
})
