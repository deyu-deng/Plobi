import { useEffect, useState } from 'react'

export interface FileBytesState {
  bytes: null | Uint8Array
  error: boolean
  loading: boolean
}

export async function readFileBytes(path: string): Promise<Uint8Array> {
  const dataUrl = await window.plobiDesktop.readFileDataUrl(path)
  const comma = dataUrl.indexOf(',')

  if (comma < 0) {
    throw new Error('Invalid data URL')
  }

  const header = dataUrl.slice(0, comma)
  const payload = dataUrl.slice(comma + 1)

  if (header.includes(';base64')) {
    const binary = atob(payload)
    const bytes = new Uint8Array(binary.length)

    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }

    return bytes
  }

  return new TextEncoder().encode(decodeURIComponent(payload))
}

export function useFileBytes(path: string, reloadKey = 0): FileBytesState {
  const [state, setState] = useState<FileBytesState>({ bytes: null, error: false, loading: true })

  useEffect(() => {
    let active = true
    setState({ bytes: null, error: false, loading: true })

    void readFileBytes(path)
      .then(bytes => {
        if (active) {
          setState({ bytes, error: false, loading: false })
        }
      })
      .catch(() => {
        if (active) {
          setState({ bytes: null, error: true, loading: false })
        }
      })

    return () => {
      active = false
    }
  }, [path, reloadKey])

  return state
}
