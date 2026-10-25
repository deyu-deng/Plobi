import type { XlsxEditorApi } from '@betteroffice/xlsx-react'

export interface OfficeXlsxSession {
  api: XlsxEditorApi
  path: string
}

const sessions = new Map<string, OfficeXlsxSession>()

export function registerXlsxSession(path: string, api: XlsxEditorApi): () => void {
  sessions.set(path, { api, path })

  return () => {
    if (sessions.get(path)?.api === api) {
      sessions.delete(path)
    }
  }
}

export function getXlsxSession(path: string): null | OfficeXlsxSession {
  return sessions.get(path) ?? null
}

export function listXlsxSessions(): OfficeXlsxSession[] {
  return [...sessions.values()]
}
