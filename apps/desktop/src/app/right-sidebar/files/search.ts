import type { PlobiReadDirResult } from '@/global'
import { readDesktopDir } from '@/lib/desktop-fs'
import { ALWAYS_EXCLUDED } from '@/lib/excluded-paths'

export interface ProjectFileSearchResult {
  name: string
  path: string
  relativeDir: string
}

export interface ProjectFileSearch {
  results: ProjectFileSearchResult[]
  scannedDirs: number
  truncated: boolean
}

export interface ProjectFileSearchOptions {
  concurrency?: number
  directoryLimit?: number
  maxDepth?: number
  resultLimit?: number
}

interface SearchDirectory {
  depth: number
  path: string
  relativeDir: string
}

type ReadDir = (path: string) => Promise<PlobiReadDirResult>

const DEFAULTS = {
  concurrency: 8,
  directoryLimit: 160,
  maxDepth: 12,
  resultLimit: 120
}

export async function searchProjectFiles(
  root: string,
  query: string,
  options: ProjectFileSearchOptions = {},
  readDir: ReadDir = readDesktopDir
): Promise<ProjectFileSearch> {
  const needle = query.trim().toLowerCase()

  if (!needle || !root) {
    return { results: [], scannedDirs: 0, truncated: false }
  }

  const concurrency = options.concurrency ?? DEFAULTS.concurrency
  const directoryLimit = options.directoryLimit ?? DEFAULTS.directoryLimit
  const maxDepth = options.maxDepth ?? DEFAULTS.maxDepth
  const resultLimit = options.resultLimit ?? DEFAULTS.resultLimit
  const queue: SearchDirectory[] = [{ depth: 0, path: root, relativeDir: '' }]
  const seen = new Set<string>()
  const results: ProjectFileSearchResult[] = []
  let scannedDirs = 0

  const worker = async () => {
    while (true) {
      if (scannedDirs >= directoryLimit || results.length >= resultLimit) {
        return
      }

      const next = queue.shift()

      if (!next) {
        return
      }

      scannedDirs += 1

      let listing: PlobiReadDirResult

      try {
        listing = await readDir(next.path)
      } catch {
        continue
      }

      for (const entry of listing.entries ?? []) {
        if (ALWAYS_EXCLUDED.has(entry.name) || seen.has(entry.path)) {
          continue
        }

        const relative = next.relativeDir ? `${next.relativeDir}/${entry.name}` : entry.name

        if (entry.isDirectory) {
          if (next.depth < maxDepth) {
            seen.add(entry.path)
            queue.push({ depth: next.depth + 1, path: entry.path, relativeDir: relative })
          }

          continue
        }

        if (!relative.toLowerCase().includes(needle)) {
          continue
        }

        seen.add(entry.path)
        results.push({ name: entry.name, path: entry.path, relativeDir: next.relativeDir })

        if (results.length >= resultLimit) {
          break
        }
      }
    }
  }

  await Promise.all(Array.from({ length: Math.max(1, concurrency) }, worker))

  results.sort((a, b) =>
    `${a.relativeDir}/${a.name}`.toLowerCase().localeCompare(`${b.relativeDir}/${b.name}`.toLowerCase())
  )

  return {
    results: results.slice(0, resultLimit),
    scannedDirs,
    truncated: queue.length > 0 || results.length >= resultLimit
  }
}
