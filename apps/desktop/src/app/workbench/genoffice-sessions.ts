type CloseHandler = () => Promise<boolean>

const handlers = new Map<string, CloseHandler>()

function keyForPath(path: string) {
  return path.replace(/\\/g, '/')
}

export function registerGenOfficeCloseHandler(path: string, handler: CloseHandler) {
  const key = keyForPath(path)
  handlers.set(key, handler)

  return () => {
    if (handlers.get(key) === handler) {
      handlers.delete(key)
    }
  }
}

/**
 * Ask the live GenOffice editor for this path whether it is safe to close.
 * The editor owns the native save / discard / cancel prompt; a missing editor
 * (not mounted, old preload) means there is nothing unsaved to protect.
 */
export async function requestGenOfficeClose(path: string): Promise<boolean> {
  const handler = handlers.get(keyForPath(path))

  if (!handler) {
    return true
  }

  try {
    return await handler()
  } catch {
    return true
  }
}
