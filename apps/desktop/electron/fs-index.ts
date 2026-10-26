import fs from 'node:fs'
import path from 'node:path'

import { FS_HIDDEN_NAMES } from './fs-noise'
import { resolveDirectoryForIpc } from './hardening'

const INDEX_TTL_MS = 5 * 60_000
const MAX_DIRECTORIES = 6_000
const MAX_DEPTH = 24
const MAX_FILES = 50_000
const MAX_RESULTS = 120

interface IndexEntry {
  name: string
  path: string
  relative: string
}

interface WorkspaceIndex {
  builtAt: number
  entries: IndexEntry[]
  truncated: boolean
}

export interface WorkspaceFileSearchResult {
  indexedFiles: number
  results: Array<{ name: string; path: string; relativeDir: string }>
  truncated: boolean
}

const indexCache = new Map<string, WorkspaceIndex>()

function cacheKey(root: string) {
  return path.resolve(root)
}

async function buildIndex(root: string, fsImpl: typeof fs): Promise<WorkspaceIndex> {
  const entries: IndexEntry[] = []
  const stack: Array<{ depth: number; dir: string; relative: string }> = [{ depth: 0, dir: root, relative: '' }]
  let directories = 0
  let truncated = false

  while (stack.length > 0) {
    if (directories >= MAX_DIRECTORIES || entries.length >= MAX_FILES) {
      truncated = true
      break
    }

    const next = stack.pop()!

    if (!next) {
      break
    }

    directories += 1

    let dirents: fs.Dirent[]

    try {
      dirents = await fsImpl.promises.readdir(next.dir, { withFileTypes: true })
    } catch {
      continue
    }

    for (const dirent of dirents) {
      if (FS_HIDDEN_NAMES.has(dirent.name) || dirent.isSymbolicLink()) {
        continue
      }

      const relative = next.relative ? `${next.relative}/${dirent.name}` : dirent.name
      const fullPath = path.join(next.dir, dirent.name)

      if (dirent.isDirectory()) {
        if (next.depth < MAX_DEPTH) {
          stack.push({ depth: next.depth + 1, dir: fullPath, relative })
        }

        continue
      }

      if (!dirent.isFile()) {
        continue
      }

      entries.push({ name: dirent.name, path: fullPath, relative })

      if (entries.length >= MAX_FILES) {
        truncated = true
        break
      }
    }
  }

  entries.sort((a, b) => a.relative.localeCompare(b.relative))

  return { builtAt: Date.now(), entries, truncated }
}

function relativeDir(entry: IndexEntry) {
  return entry.relative.length > entry.name.length
    ? entry.relative.slice(0, -entry.name.length - 1)
    : ''
}

export async function searchWorkspaceIndex(
  root: string,
  query: string,
  options: { fsImpl?: typeof fs; refresh?: boolean } = {}
): Promise<WorkspaceFileSearchResult> {
  const fsImpl = options.fsImpl ?? fs
  const key = cacheKey(root)
  const cached = indexCache.get(key)
  let index = cached

  if (!index || options.refresh || Date.now() - index.builtAt > INDEX_TTL_MS) {
    const { resolvedPath } = await resolveDirectoryForIpc(root, { fs: fsImpl, purpose: 'Workspace index' })
    index = await buildIndex(resolvedPath, fsImpl)
    indexCache.set(key, index)
  }

  const needle = query.trim().toLowerCase()

  if (!needle) {
    return { indexedFiles: index.entries.length, results: [], truncated: index.truncated }
  }

  const matches = index.entries.filter(entry => entry.relative.toLowerCase().includes(needle))

  return {
    indexedFiles: index.entries.length,
    results: matches.slice(0, MAX_RESULTS).map(entry => ({
      name: entry.name,
      path: entry.path,
      relativeDir: relativeDir(entry)
    })),
    truncated: index.truncated || matches.length > MAX_RESULTS
  }
}

export function invalidateWorkspaceIndex(root?: string) {
  if (!root) {
    indexCache.clear()

    return
  }

  indexCache.delete(cacheKey(root))
}
