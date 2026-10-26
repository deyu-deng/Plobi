import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, it } from 'node:test'

import { statFileForIpc } from './fs-stat'

let root = ''

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-fs-stat-'))
})

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true })
})

describe('statFileForIpc', () => {
  it('returns size and mtime for a real file', async () => {
    const file = path.join(root, 'a.txt')
    fs.writeFileSync(file, 'hello')

    const result = await statFileForIpc(file)

    assert.equal(result.error, undefined)
    assert.equal(result.size, 5)
    assert.ok(result.mtimeMs > 0)
  })

  it('reports a missing file instead of throwing', async () => {
    const result = await statFileForIpc(path.join(root, 'missing.txt'))

    assert.equal(result.error, 'ENOENT')
  })

  it('rejects a directory', async () => {
    const result = await statFileForIpc(root)

    assert.equal(result.error, 'ENOTFILE')
  })
})
