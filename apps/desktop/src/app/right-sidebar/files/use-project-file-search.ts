import { useEffect, useState } from 'react'

import { type ProjectFileSearch, searchProjectFiles } from './search'

const EMPTY: ProjectFileSearch & { loading: boolean } = {
  loading: false,
  results: [],
  scannedDirs: 0,
  truncated: false
}

const SEARCH_DEBOUNCE_MS = 160

export function useProjectFileSearch(root: string, query: string): ProjectFileSearch & { loading: boolean } {
  const [state, setState] = useState(EMPTY)

  useEffect(() => {
    const needle = query.trim()

    if (!root || !needle) {
      setState(EMPTY)

      return
    }

    let cancelled = false
    setState({ ...EMPTY, loading: true })

    const timer = window.setTimeout(() => {
      void searchProjectFiles(root, needle).then(
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
  }, [query, root])

  return state
}
