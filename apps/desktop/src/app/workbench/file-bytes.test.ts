import { afterEach, describe, expect, it, vi } from 'vitest'

import { readFileBytes } from './file-bytes'

// 用户 11-05：「PDF 又坏掉了」。教材是 46 MB / 124 MB，而旧路走的是
// `readFileDataUrl` —— base64 字符串 + 主进程 16 MB 上限 ⇒ 一律 EFBIG，
// 屏上只剩「This file cannot be previewed yet」。
// 这条用例盯的是「预览不再走 data URL，改走二进制 IPC」。
describe('readFileBytes', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reads bytes over the binary IPC instead of a base64 data URL', async () => {
    const payload = new Uint8Array([1, 2, 3, 250])
    const readFileBytesIpc = vi.fn(async () => payload.buffer)
    const readFileDataUrl = vi.fn(async () => 'data:application/pdf;base64,AAAA')

    vi.stubGlobal('window', { ...window, plobiDesktop: { readFileBytes: readFileBytesIpc, readFileDataUrl } })

    const bytes = await readFileBytes('D:/courses/textbook.pdf')

    expect(Array.from(bytes)).toEqual([1, 2, 3, 250])
    expect(readFileBytesIpc).toHaveBeenCalledWith('D:/courses/textbook.pdf')
    expect(readFileDataUrl).not.toHaveBeenCalled()
  })

  it('accepts a typed-array view as well as a bare ArrayBuffer', async () => {
    const view = new Uint8Array([7, 8, 9])

    vi.stubGlobal('window', { ...window, plobiDesktop: { readFileBytes: async () => view } })

    expect(Array.from(await readFileBytes('D:/model.glb'))).toEqual([7, 8, 9])
  })
})
