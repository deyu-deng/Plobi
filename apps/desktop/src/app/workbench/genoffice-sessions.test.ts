import { describe, expect, it, vi } from 'vitest'

import { registerGenOfficeCloseHandler, requestGenOfficeClose } from './genoffice-sessions'

describe('genoffice close handlers', () => {
  it('lets the live editor veto a close', async () => {
    const unregister = registerGenOfficeCloseHandler('/tmp/a.xlsx', async () => false)

    await expect(requestGenOfficeClose('/tmp/a.xlsx')).resolves.toBe(false)
    unregister()
  })

  it('normalizes Windows separators', async () => {
    const unregister = registerGenOfficeCloseHandler('D:\\work\\a.docx', async () => true)

    await expect(requestGenOfficeClose('D:/work/a.docx')).resolves.toBe(true)
    unregister()
  })

  it('returns true when no editor is mounted or the handler throws', async () => {
    await expect(requestGenOfficeClose('/tmp/missing.pptx')).resolves.toBe(true)

    const unregister = registerGenOfficeCloseHandler('/tmp/bad.xlsx', async () => {
      throw new Error('gone')
    })
    await expect(requestGenOfficeClose('/tmp/bad.xlsx')).resolves.toBe(true)
    unregister()
  })

  it('removes only its own registration', async () => {
    const first = vi.fn(async () => false)
    const second = vi.fn(async () => true)
    const unregisterFirst = registerGenOfficeCloseHandler('/tmp/same.xlsx', first)
    const unregisterSecond = registerGenOfficeCloseHandler('/tmp/same.xlsx', second)

    unregisterFirst()
    await expect(requestGenOfficeClose('/tmp/same.xlsx')).resolves.toBe(true)
    unregisterSecond()
    await expect(requestGenOfficeClose('/tmp/same.xlsx')).resolves.toBe(true)
  })
})
