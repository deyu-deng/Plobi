import { memo, useState } from 'react'

import { StatusRow } from '@/components/chat/status-row'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { normalizeOrLocalPreviewTarget } from '@/lib/local-preview'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { setCurrentSessionPreviewTarget } from '@/store/preview'
import { type PreviewArtifact } from '@/store/preview-status'

interface PreviewStatusRowProps {
  item: PreviewArtifact
  onDismiss: (id: string) => void
}

/** One detected artifact, single line, always visible: filename + open + close. */
export const PreviewStatusRow = memo(function PreviewStatusRow({ item, onDismiss }: PreviewStatusRowProps) {
  const { t } = useI18n()
  const [opening, setOpening] = useState(false)

  const resolveTarget = async () => {
    const target = await normalizeOrLocalPreviewTarget(item.target, item.cwd || undefined)

    if (!target) {
      throw new Error(`Could not open preview target: ${item.target}`)
    }

    return target
  }

  // 预览落在**工作区卡片的一个页签**里（预览轨删了，用户 11-09 11:47）⇒
  // 这里只管「开」，关是那条页签自己的 X。
  const openPreview = async () => {
    if (opening) {
      return
    }

    setOpening(true)

    try {
      setCurrentSessionPreviewTarget(await resolveTarget(), 'tool-result', item.target)
    } catch (error) {
      notifyError(error, t.preview.unavailable)
    } finally {
      setOpening(false)
    }
  }

  const openInBrowser = async () => {
    try {
      const bridge = window.plobiDesktop?.openPreviewInBrowser

      if (!bridge) {
        throw new Error('Desktop preview browser bridge is unavailable')
      }

      await bridge((await resolveTarget()).url)
    } catch (error) {
      notifyError(error, t.preview.unavailable)
    }
  }

  return (
    <StatusRow
      leading={
        <Codicon
          aria-hidden
          className={cn('text-muted-foreground/70', opening && 'animate-pulse')}
          name="globe"
          size="0.8rem"
        />
      }
      // Plain click opens the link in the browser; ⌘/Ctrl-click opens it as a
      // tab of the workbench card instead.
      onActivate={event => {
        if (event.metaKey || event.ctrlKey) {
          void openPreview()
        } else {
          void openInBrowser()
        }
      }}
      trailing={
        <Tip label={t.statusStack.dismiss}>
          <Button
            aria-label={t.statusStack.dismiss}
            className="-my-1 size-4 rounded-md text-muted-foreground/60 hover:text-foreground/90"
            onClick={event => {
              event.stopPropagation()
              onDismiss(item.id)
            }}
            size="icon-xs"
            type="button"
            variant="ghost"
          >
            <Codicon name="close" size="0.75rem" />
          </Button>
        </Tip>
      }
      trailingVisible
    >
      <Tip
        label={
          <span className="flex flex-col gap-0.5">
            <span>{item.target}</span>
            <span className="opacity-70">{t.preview.linkHint}</span>
          </span>
        }
      >
        <span className="min-w-0 max-w-[18rem] truncate text-[0.73rem] leading-4 text-foreground/92">{item.label}</span>
      </Tip>
    </StatusRow>
  )
})
