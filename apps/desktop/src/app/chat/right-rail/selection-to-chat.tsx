import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

import { Button } from '@/components/ui/button'
import { KbdCombo } from '@/components/ui/kbd'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { previewSelectionLabel } from '@/store/preview'

const BUTTON_WIDTH = 132
const BUTTON_HEIGHT = 34
const EDGE = 8

/**
 * Place the floating button just below the selection, clamped inside the rail so a
 * selection near an edge cannot push it off-screen. Split out (and exported)
 * because jsdom reports no layout for a real Range, so the maths is what is worth
 * testing here rather than the event plumbing.
 */
export function anchorForSelection(
  host: HTMLDivElement,
  rect: { bottom: number; left: number }
): { left: number; top: number } {
  const hostRect = host.getBoundingClientRect()

  return {
    left: Math.min(Math.max(rect.left - hostRect.left, EDGE), Math.max(EDGE, host.clientWidth - BUTTON_WIDTH)),
    top: Math.min(
      Math.max(rect.bottom - hostRect.top + 4, EDGE),
      Math.max(EDGE, host.clientHeight - BUTTON_HEIGHT)
    )
  }
}

interface SelectionToChatProps {
  /** Selections outside this element are ignored — the rail must not claim the composer. */
  containerRef: RefObject<HTMLDivElement | null>
  onAddSelectionToChat: (text: string, label: string) => void
}

/**
 * Floating "add to chat" for a text selection inside the right rail
 * (WP-MIND-NOTES-VIEW).
 *
 * ⌘/Ctrl+L already carries a preview-pane selection to the composer — this makes
 * that invisible path visible, reusing the same handler and the same file-name
 * label, so there is one behaviour with two affordances. Anchoring is by DOM
 * range rather than the terminal's `.xterm-selection` rectangles, which do not
 * exist outside xterm.
 */
export function SelectionToChat({ containerRef, onAddSelectionToChat }: SelectionToChatProps) {
  const { t } = useI18n()
  const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(null)
  const textRef = useRef('')

  useEffect(() => {
    const sync = () => {
      const host = containerRef.current
      const selection = window.getSelection()
      const text = selection?.toString().trim() ?? ''

      if (!host || !selection?.rangeCount || !text) {
        setAnchor(null)

        textRef.current = ''

        return
      }

      const range = selection.getRangeAt(0)

      if (!host.contains(range.commonAncestorContainer)) {
        setAnchor(null)

        textRef.current = ''

        return
      }

      const rect = range.getBoundingClientRect()

      if (!rect.width && !rect.height) {
        setAnchor(null)

        textRef.current = ''

        return
      }

      textRef.current = text
      setAnchor(anchorForSelection(host, rect))
    }

    document.addEventListener('mouseup', sync)
    document.addEventListener('keyup', sync)

    return () => {
      document.removeEventListener('mouseup', sync)
      document.removeEventListener('keyup', sync)
    }
  }, [containerRef])

  if (!anchor) {
    return null
  }

  return (
    <div className="pointer-events-none absolute z-50" style={{ left: anchor.left, top: anchor.top }}>
      <Button
        className={cn('pointer-events-auto h-6 rounded-md px-2 text-[0.68rem] shadow-md backdrop-blur-md')}
        data-testid="rail-selection-to-chat"
        onMouseDown={event => {
          // preventDefault keeps the browser from clearing the selection first.
          event.preventDefault()
          event.stopPropagation()
          const text = textRef.current

          textRef.current = ''
          setAnchor(null)

          if (text) {
            onAddSelectionToChat(text, previewSelectionLabel() || 'selection')
          }
        }}
        size="sm"
        type="button"
        variant="secondary"
      >
        {t.rightSidebar.addToChat}
        <KbdCombo className="ml-1 opacity-70" combo="mod+l" size="sm" />
      </Button>
    </div>
  )
}
