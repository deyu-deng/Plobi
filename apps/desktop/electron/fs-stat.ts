import fs from 'node:fs'

import { resolveRequestedPathForIpc } from './hardening'

export interface FileStatResult {
  error?: string
  mtimeMs: number
  size: number
}

export async function statFileForIpc(filePath: string): Promise<FileStatResult> {
  try {
    const resolved = resolveRequestedPathForIpc(filePath, { purpose: 'File watch' })
    const stat = await fs.promises.stat(resolved)

    if (!stat.isFile()) {
      return { error: 'ENOTFILE', mtimeMs: 0, size: 0 }
    }

    return { mtimeMs: stat.mtimeMs, size: stat.size }
  } catch (error) {
    return {
      error: (error as NodeJS.ErrnoException)?.code || 'stat-error',
      mtimeMs: 0,
      size: 0
    }
  }
}
