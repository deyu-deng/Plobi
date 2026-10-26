import { useEffect, useState } from 'react'

import { isDesktopFsRemoteMode, searchDesktopWorkspaceFiles } from '@/lib/desktop-fs'

import { type ProjectFileSearch, searchProjectFilesWithIndex } from './search'

const EMPTY: ProjectFileSearch & { loading: boolean } = {
  indexedFiles: undefined,
  loading: false,
  results: [],
  scannedDirs: 0,
  truncated: false
}

const SEARCH_DEBOUNCE_MS = 160

export function useProjectFileSearch(
  root: string,
  query: string,
  refreshToken = 0
): ProjectFileSearch & { loading: boolean } {
  const [state, setState] = useState(EMPTY)

  // Warm the workspace index when the files tab mounts: the first typed query
  // then filters an in-memory list instead of walking the disk.
  useEffect(() => {
    if (!root || isDesktopFsRemoteMode() || !window.plobiDesktop?.searchWorkspaceFiles) {
      return
    }

    const timer = window.setTimeout(() => {
      void searchDesktopWorkspaceFiles(root, '').catch(() => {})
    }, 400)

    return () => window.clearTimeout(timer)
  }, [root])

  useEffect(() => {
    const needle = query.trim()

    if (!root || !needle) {
      setState(EMPTY)

      return
    }

    let cancelled = false
    setState({ ...EMPTY, loading: true })

    const timer = window.setTimeout(() => {
      void searchProjectFilesWithIndex(root, needle).then(
        next => {
          if (!cancelled) {
            setState({ ...next, loading: false })
          }
        },
        () => {
          if (!cancelled) {
            setState(EMPTY)
          }
        }
      )
    }, SEARCH_DEBOUNCE_MS)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, refreshToken, root])

  return state
}
