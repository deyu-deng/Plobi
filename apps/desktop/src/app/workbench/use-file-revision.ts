import { useEffect, useRef, useState } from 'react'

import { isDesktopFsRemoteMode, statDesktopFile } from '@/lib/desktop-fs'

export interface FileRevision {
  mtimeMs: number
  size: number
}

const DEFAULT_INTERVAL_MS = 1500

/**
 * Polls an open file's revision and bumps `change` when another writer (an
 * agent, OfficeCLI, an external editor) saves it. The initial read only seeds
 * the baseline — mounting a tab never looks like an external change.
 */
export function useFileRevision(
  path: string,
  intervalMs = DEFAULT_INTERVAL_MS
): { change: number; revision: FileRevision | null } {
  const [revision, setRevision] = useState<FileRevision | null>(null)
  const [change, setChange] = useState(0)
  const lastToken = useRef('')

  useEffect(() => {
    setChange(0)
    setRevision(null)
    lastToken.current = ''

    if (!path || isDesktopFsRemoteMode()) {
      return
    }

    let cancelled = false

    const tick = async () => {
      const stat = await statDesktopFile(path)

      if (cancelled || !stat) {
        return
      }

      const token = `${stat.mtimeMs}:${stat.size}`

      if (lastToken.current && lastToken.current !== token) {
        setChange(value => value + 1)
      }

      lastToken.current = token
      setRevision(stat)
    }

    void tick()
    const timer = window.setInterval(() => void tick(), intervalMs)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [intervalMs, path])

  return { change, revision }
}
