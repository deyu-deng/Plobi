import { useEffect, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'
import { openExternalLink } from '@/lib/external-link'
import {
  gatewayMediaDataUrl,
  isRemoteGateway,
  mediaExternalUrl,
  mediaKind,
  mediaName,
  mediaStreamUrl
} from '@/lib/media'

import type { OfficePreviewLabels } from './office-preview'
import { useFileRevision } from './use-file-revision'

export type RichPreviewLabels = Pick<
  OfficePreviewLabels,
  'loading' | 'openExternal' | 'readOnly' | 'unavailable'
>

export function MediaPreview({ labels, path }: { labels: RichPreviewLabels; path: string }) {
  const { change } = useFileRevision(path)
  const kind = mediaKind(path)
  const name = mediaName(path)
  const [src, setSrc] = useState('')
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    setSrc('')
    setFailed(false)

    if (kind !== 'audio' && kind !== 'video') {
      setFailed(true)

      return () => {
        active = false
      }
    }

    if (!isRemoteGateway()) {
      setSrc(mediaStreamUrl(path))

      return () => {
        active = false
      }
    }

    void gatewayMediaDataUrl(path)
      .then(value => {
        if (active) {
          setSrc(value)
        }
      })
      .catch(() => {
        if (active) {
          setFailed(true)
        }
      })

    return () => {
      active = false
    }
  }, [kind, path])

  const open = () => openExternalLink(mediaExternalUrl(path))

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-(--ui-bg-primary)">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2">
        <span className="min-w-0 flex-1 truncate text-[0.66rem] text-(--ui-text-secondary)">{name}</span>
        <button
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[0.6rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          onClick={open}
          title={labels.openExternal}
          type="button"
        >
          <Codicon name="link-external" size="0.72rem" />
          <span>{labels.openExternal}</span>
        </button>
        <span className="shrink-0 text-[0.6rem] text-muted-foreground">{labels.readOnly}</span>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-3">
        {failed ? (
          <div className="flex max-w-sm flex-col items-center gap-2 text-center">
            <Codicon name="warning" size="1.25rem" />
            <p className="text-[0.8rem] font-medium text-foreground">{labels.unavailable}</p>
            <button
              className="rounded border border-(--ui-stroke-secondary) px-2 py-1 text-[0.66rem] hover:bg-(--ui-row-hover-background)"
              onClick={open}
              type="button"
            >
              {labels.openExternal}
            </button>
          </div>
        ) : !src ? (
          <PageLoader label={labels.loading} />
        ) : kind === 'video' ? (
          <video
            className="max-h-full max-w-full rounded-lg bg-black shadow-sm"
            controls
            key={`${path}:${change}`}
            onError={() => setFailed(true)}
            preload="metadata"
            src={src}
          />
        ) : (
          <div className="w-full max-w-md rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) p-4">
            <div className="mb-2 flex items-center gap-2">
              <Codicon name="play-circle" size="1rem" />
              <span className="min-w-0 flex-1 truncate text-[0.72rem] font-medium">{name}</span>
            </div>
            <audio
              className="block w-full"
              controls
              key={`${path}:${change}`}
              onError={() => setFailed(true)}
              preload="metadata"
              src={src}
            />
          </div>
        )}
      </div>
    </div>
  )
}
