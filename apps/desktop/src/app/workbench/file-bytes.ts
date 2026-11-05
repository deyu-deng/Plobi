import { useEffect, useState } from 'react'

export interface FileBytesState {
  bytes: null | Uint8Array
  error: boolean
  loading: boolean
}

/**
 * Whole file as bytes, for the previews that must parse the entire buffer
 * (pdf.js, 3D loaders, zip listings).
 *
 * 走二进制 IPC，不走 data URL：data URL 是 base64，字符串比文件大三分之一、还要
 * 再解一遍，所以主进程那边卡在 16 MB —— 这台机器上的教材是 46 MB 和 124 MB，
 * 于是「打开 PDF」变成「This file cannot be previewed yet」（用户 11-05 复报）。
 * `plobi:readFileBytes` 直接把 ArrayBuffer 结构化克隆过来，上限是 256 MB。
 */
export async function readFileBytes(path: string): Promise<Uint8Array> {
  const raw = (await window.plobiDesktop.readFileBytes(path)) as unknown

  if (raw instanceof Uint8Array) {
    return raw
  }

  return new Uint8Array(raw as ArrayBuffer)
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
