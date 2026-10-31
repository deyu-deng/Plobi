import { useCallback, useState } from 'react'
import type { DragEvent } from 'react'

/**
 * 卡片互换（用户 10-31）：按住对话卡或右栏卡的抬头拖到另一张上，两张卡换位。
 *
 * 拖动状态放在模块变量里而不是 nanostore：它只在一次 drag 手势里有效，
 * 不跨渲染、不该被持久化（也不是界面要观察的状态）。落点只认「另一张卡」。
 */
export type ShellCardKind = 'chat' | 'rail'

let draggingKind: ShellCardKind | null = null

export function cardDragProps(kind: ShellCardKind) {
  return {
    draggable: true,
    onDragEnd: () => {
      draggingKind = null
    },
    onDragStart: (event: DragEvent) => {
      draggingKind = kind
      event.dataTransfer.effectAllowed = 'move'
      event.dataTransfer.setData('text/plain', kind)
    }
  }
}

export function useCardDrop(kind: ShellCardKind, onSwap: () => void) {
  const [over, setOver] = useState(false)
  const clear = useCallback(() => setOver(false), [])

  return {
    dropProps: {
      onDragLeave: (event: DragEvent) => {
        // 指针还在卡内、只是从子元素上掠过时不算离开，否则虚线框会闪
        const next = event.relatedTarget as Node | null
        if (next && event.currentTarget.contains(next)) {
          return
        }

        clear()
      },
      onDragOver: (event: DragEvent) => {
        if (!draggingKind || draggingKind === kind) {
          return
        }

        event.preventDefault()
        event.dataTransfer.dropEffect = 'move'

        if (!over) {
          setOver(true)
        }
      },
      onDrop: (event: DragEvent) => {
        if (!draggingKind || draggingKind === kind) {
          return
        }

        event.preventDefault()
        draggingKind = null
        clear()
        onSwap()
      }
    },
    over
  }
}
