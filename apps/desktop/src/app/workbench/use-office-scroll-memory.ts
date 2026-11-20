import { useCallback, useEffect, useState } from 'react'

import { officeViewStateKey, parseOfficeViewSnapshot } from './office-view-state'

/**
 * 宿主自己渲染的预览（PDF、xls 表格）的阅读位置记忆。
 *
 * 这两条链不跑 webview，滚动容器就在 React 树里，所以直接拿节点读写 scrollTop，
 * 不用注入脚本。存的是「滚动比例」而不是像素：窗口宽度一变，页高和总高度都会跳，
 * 像素必然错位。
 *
 * 返回的是 callback ref 而不是 RefObject：PDF 那条链的 div 上已经挂着渲染用的
 * hostRef，两者要自己合并，callback 形式拼起来最省事。
 */

const RESTORE_WINDOW_MS = 15_000
const SAVE_THROTTLE_MS = 400
const MIN_SCROLL_ROOM = 24

function readRatio(key: string): number | null {
  try {
    const snapshot = parseOfficeViewSnapshot(window.localStorage.getItem(key))

    return snapshot ? snapshot.ratio : null
  } catch {
    return null
  }
}

function writeRatio(key: string, el: HTMLElement): void {
  const room = el.scrollHeight - el.clientHeight

  if (room < MIN_SCROLL_ROOM) {
    return
  }

  const ratio = Math.min(1, Math.max(0, el.scrollTop / room))

  try {
    window.localStorage.setItem(key, JSON.stringify({ at: Date.now(), ratio }))
  } catch {
    /* 配额满了就算了，位置不是数据 */
  }
}

export function useOfficeScrollMemory({
  kind,
  path
}: {
  kind: string
  path: string
}): (node: HTMLDivElement | null) => void {
  const [el, setEl] = useState<HTMLDivElement | null>(null)

  // attach 必须保持身份稳定：PDF 那条链会把它合并进一个 ref 回调里，一旦身份每次
  // 渲染都换新，React 就会先传 null 再传节点，恢复逻辑被反复触发、滚动位置被按回原位。
  const attach = useCallback((node: HTMLDivElement | null) => {
    setEl(prev => (prev === node ? prev : node))
  }, [])

  useEffect(() => {
    if (!el || !path) {
      return
    }

    const key = officeViewStateKey(kind, path)
    const saved = readRatio(key)
    let restoring = saved !== null
    let disposed = false
    let saveTimer = 0
    let restoreTimer = 0

    const save = () => writeRatio(key, el)

    const onScroll = () => {
      if (restoring || saveTimer) {
        return
      }

      saveTimer = window.setTimeout(() => {
        saveTimer = 0
        save()
      }, SAVE_THROTTLE_MS)
    }

    // 用户自己动手就交出控制权，别把人家按回原位。
    const onUserIntent = () => {
      restoring = false
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onUserIntent, { passive: true })
    el.addEventListener('pointerdown', onUserIntent, { passive: true })

    if (saved !== null) {
      const startedAt = Date.now()
      let stableHits = 0

      const step = () => {
        if (disposed || !restoring) {
          return
        }

        const room = el.scrollHeight - el.clientHeight

        if (room >= MIN_SCROLL_ROOM) {
          const want = saved * room

          if (Math.abs(el.scrollTop - want) <= 2) {
            stableHits += 1
          } else {
            el.scrollTop = want
            stableHits = 0
          }
        }

        const elapsed = Date.now() - startedAt

        // PDF 是一页一页画的、表格是整块渲染，两种都可能在首次排版后又长高一次。
        if ((stableHits >= 2 && elapsed > 400) || elapsed > RESTORE_WINDOW_MS) {
          restoring = false

          return
        }

        restoreTimer = window.setTimeout(step, 120)
      }

      step()
    }

    return () => {
      disposed = true
      restoring = false

      if (saveTimer) {
        window.clearTimeout(saveTimer)
      }

      if (restoreTimer) {
        window.clearTimeout(restoreTimer)
      }

      save()

      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onUserIntent)
      el.removeEventListener('pointerdown', onUserIntent)
    }
  }, [el, kind, path])

  return attach
}
