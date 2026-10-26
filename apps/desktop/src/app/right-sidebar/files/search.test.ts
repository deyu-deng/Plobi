import { describe, expect, it, vi } from 'vitest'

import { searchProjectFiles } from './search'

const tree: Record<string, { name: string; path: string; isDirectory: boolean }[]> = {
  '/root': [
    { name: 'src', path: '/root/src', isDirectory: true },
    { name: 'node_modules', path: '/root/node_modules', isDirectory: true },
    { name: 'README.md', path: '/root/README.md', isDirectory: false }
  ],
  '/root/src': [
    { name: 'app.ts', path: '/root/src/app.ts', isDirectory: false },
    { name: 'comp', path: '/root/src/comp', isDirectory: true }
  ],
  '/root/src/comp': [{ name: 'Button.tsx', path: '/root/src/comp/Button.tsx', isDirectory: false }],
  '/root/node_modules': [{ name: 'dep.ts', path: '/root/node_modules/dep.ts', isDirectory: false }]
}

function reader() {
  return vi.fn(async (path: string) => ({ entries: tree[path] ?? [] }))
}

describe('searchProjectFiles', () => {
  it('returns matching files with their relative folder', async () => {
    const readDir = reader()
    const result = await searchProjectFiles('/root', 'button', {}, readDir)

    expect(result.results).toEqual([
      { name: 'Button.tsx', path: '/root/src/comp/Button.tsx', relativeDir: 'src/comp' }
    ])
    expect(result.truncated).toBe(false)
  })

  it('matches the relative path, not just the file name', async () => {
    const result = await searchProjectFiles('/root', 'src/comp', {}, reader())

    expect(result.results.map(item => item.name)).toEqual(['Button.tsx'])
  })

  it('never walks always-excluded folders', async () => {
    const readDir = reader()
    const result = await searchProjectFiles('/root', 'dep', {}, readDir)

    expect(result.results).toEqual([])
    expect(readDir).not.toHaveBeenCalledWith('/root/node_modules')
  })

  it('reports truncation when the directory budget runs out', async () => {
    const result = await searchProjectFiles('/root', 'ts', { directoryLimit: 1 }, reader())

    expect(result.truncated).toBe(true)
    expect(result.scannedDirs).toBe(1)
  })

  it('returns nothing for an empty query without reading the disk', async () => {
    const readDir = reader()

    expect(await searchProjectFiles('/root', '   ', {}, readDir)).toEqual({
      results: [],
      scannedDirs: 0,
      truncated: false
    })
    expect(readDir).not.toHaveBeenCalled()
  })
})
