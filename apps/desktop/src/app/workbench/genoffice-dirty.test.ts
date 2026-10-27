import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  officeDiskChangeAction,
  queryGenOfficeDirty,
  registerGenOfficeDirtyProvider
} from './genoffice-dirty'

afterEach(() => {
  vi.useRealTimers()
})

describe('officeDiskChangeAction', () => {
  it('never auto-reloads over unsaved work', () => {
    expect(officeDiskChangeAction(false, true)).toBe('prompt')
    expect(officeDiskChangeAction(true, true)).toBe('prompt')
  })

  it('reloads a clean unfocused editor', () => {
    expect(officeDiskChangeAction(false, false)).toBe('reload')
  })

  it('ignores focused writes (the editor saving its own buffer)', () => {
    expect(officeDiskChangeAction(true, false)).toBe('ignore')
  })

  it('asks when dirty state is unknown and the editor is unfocused', () => {
    expect(officeDiskChangeAction(false, null)).toBe('prompt')
    expect(officeDiskChangeAction(true, null)).toBe('ignore')
  })
})

describe('genoffice dirty providers', () => {
  it('returns null when no editor is mounted for the path', async () => {
    expect(await queryGenOfficeDirty('D:/elsewhere/a.xlsx')).toBeNull()
  })

  it('normalises Windows separators and unregisters', async () => {
    const unregister = registerGenOfficeDirtyProvider('D:\\w\\a.xlsx', async () => true)

    expect(await queryGenOfficeDirty('D:/w/a.xlsx')).toBe(true)

    unregister()

    expect(await queryGenOfficeDirty('D:/w/a.xlsx')).toBeNull()
  })

  it('falls back to null when the editor does not answer', async () => {
    vi.useFakeTimers()
    registerGenOfficeDirtyProvider('D:/w/slow.docx', () => new Promise(() => {}))
    const result = queryGenOfficeDirty('D:/w/slow.docx')

    await vi.advanceTimersByTimeAsync(1800)

    expect(await result).toBeNull()
  })
})
