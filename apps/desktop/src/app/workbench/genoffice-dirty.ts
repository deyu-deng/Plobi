export type OfficeDiskChangeAction = 'ignore' | 'prompt' | 'reload'

/**
 * A disk change while the editor is focused is usually the editor's own save
 * (autosave is off by default, so this is an explicit save). A focused editor
 * with unsaved work, however, means someone else wrote the file underneath it.
 * Unfocused + clean is the agent-edit case: reload silently.
 */
export function officeDiskChangeAction(focused: boolean, dirty: boolean | null): OfficeDiskChangeAction {
  if (dirty === true) {
    return 'prompt'
  }

  if (dirty === null) {
    return focused ? 'ignore' : 'prompt'
  }

  return focused ? 'ignore' : 'reload'
}

type DirtyProvider = () => Promise<boolean | null>

const providers = new Map<string, DirtyProvider>()

function keyForPath(path: string) {
  return path.replace(/\\/g, '/')
}

export function registerGenOfficeDirtyProvider(path: string, provider: DirtyProvider) {
  const key = keyForPath(path)
  providers.set(key, provider)

  return () => {
    if (providers.get(key) === provider) {
      providers.delete(key)
    }
  }
}

const DIRTY_QUERY_TIMEOUT_MS = 1800

function withTimeout<T>(promise: Promise<T>, fallback: T): Promise<T> {
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve(fallback), DIRTY_QUERY_TIMEOUT_MS)

    promise.then(
      value => {
        clearTimeout(timer)
        resolve(value)
      },
      () => {
        clearTimeout(timer)
        resolve(fallback)
      }
    )
  })
}

export async function queryGenOfficeDirty(path: string): Promise<boolean | null> {
  const provider = providers.get(keyForPath(path))

  if (!provider) {
    return null
  }

  return withTimeout(provider(), null)
}

export function queryGenOfficeEditorDirty(
  kind: 'slides' | 'sheets' | 'word',
  webContentsId: number | null
): Promise<boolean | null> {
  const bridge = window.plobiDesktop?.genofficeQueryDirty

  if (!bridge || !webContentsId) {
    return Promise.resolve(null)
  }

  return withTimeout(bridge(kind, webContentsId), null)
}
