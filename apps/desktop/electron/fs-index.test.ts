import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, it } from 'node:test'

import { invalidateWorkspaceIndex, searchWorkspaceIndex } from './fs-index'

let root = ''

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-fs-index-'))
  fs.mkdirSync(path.join(root, 'src', 'comp'), { recursive: true })
  fs.mkdirSync(path.join(root, 'node_modules', 'dep'), { recursive: true })
  fs.writeFileSync(path.join(root, 'README.md'), '# readme')
  fs.writeFileSync(path.join(root, 'src', 'app.ts'), 'export {}')
  fs.writeFileSync(path.join(root, 'src', 'comp', 'Button.tsx'), 'export {}')
  fs.writeFileSync(path.join(root, 'node_modules', 'dep', 'index.js'), 'module.exports = {}')
})

afterEach(() => {
  invalidateWorkspaceIndex(root)
  fs.rmSync(root, { recursive: true, force: true })
})

describe('searchWorkspaceIndex', () => {
  it('builds an index once and searches relative paths', async () => {
    const result = await searchWorkspaceIndex(root, 'button')

    assert.deepEqual(result.results, [
      {
        name: 'Button.tsx',
        path: path.join(root, 'src', 'comp', 'Button.tsx'),
        relativeDir: 'src/comp'
      }
    ])
    assert.equal(result.indexedFiles, 3)
    assert.equal(result.truncated, false)
  })

  it('warm-up with an empty query returns the indexed count', async () => {
    const result = await searchWorkspaceIndex(root, '')

    assert.deepEqual(result.results, [])
    assert.equal(result.indexedFiles, 3)
  })

  it('skips always-hidden dependency folders', async () => {
    const result = await searchWorkspaceIndex(root, 'index.js')

    assert.deepEqual(result.results, [])
    assert.equal(result.indexedFiles, 3)
  })

  it('refresh re-indexes after a file appears', async () => {
    await searchWorkspaceIndex(root, '')
    fs.writeFileSync(path.join(root, 'src', 'later.tsx'), 'export {}')

    const stale = await searchWorkspaceIndex(root, 'later')
    assert.deepEqual(stale.results, [])

    const refreshed = await searchWorkspaceIndex(root, 'later', { refresh: true })
    assert.deepEqual(
      refreshed.results.map(item => item.name),
      ['later.tsx']
    )
    assert.equal(refreshed.indexedFiles, 4)
  })
})
