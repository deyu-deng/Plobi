import { strFromU8, unzipSync } from 'fflate'
import { useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Codicon } from '@/components/ui/codicon'
import { openExternalLink } from '@/lib/external-link'
import { filePathFromMediaPath, mediaExternalUrl, mediaName } from '@/lib/media'
import { cn } from '@/lib/utils'

import { readFileBytes } from './file-bytes'
import type { RichPreviewLabels } from './media-preview'
import { useFileRevision } from './use-file-revision'

const MAX_ENTRY_BYTES = 4 * 1024 * 1024
const MAX_TOTAL_BYTES = 64 * 1024 * 1024
const MAX_PREVIEW_BYTES = 200 * 1024

const TEXT_EXTENSIONS = new Set([
  'cfg',
  'conf',
  'csv',
  'ini',
  'json',
  'log',
  'md',
  'properties',
  'py',
  'rs',
  'sh',
  'sql',
  'toml',
  'ts',
  'tsx',
  'txt',
  'xml',
  'yaml',
  'yml'
])

const IMAGE_EXTENSIONS = new Set(['gif', 'jpeg', 'jpg', 'png', 'svg', 'webp'])

interface ArchiveEntry {
  data: Uint8Array
  name: string
  size: number
}

function extensionOf(name: string) {
  return name.split('.').pop()?.toLowerCase() ?? ''
}

function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`
  }

  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }

  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function toDataUrl(mime: string, data: Uint8Array) {
  let binary = ''

  for (let index = 0; index < data.length; index += 1) {
    binary += String.fromCharCode(data[index])
  }

  return `data:${mime};base64,${btoa(binary)}`
}

function mimeForImage(name: string) {
  const ext = extensionOf(name)

  if (ext === 'svg') {
    return 'image/svg+xml'
  }

  if (ext === 'gif') {
    return 'image/gif'
  }

  if (ext === 'webp') {
    return 'image/webp'
  }

  if (ext === 'png') {
    return 'image/png'
  }

  return 'image/jpeg'
}

export function ArchivePreview({
  labels,
  path
}: {
  labels: RichPreviewLabels & { archiveEmpty: string }
  path: string
}) {
  const { change } = useFileRevision(path)
  const [entries, setEntries] = useState<ArchiveEntry[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true

    setLoading(true)
    setFailed(false)
    setEntries([])
    setSelected('')

    void readFileBytes(path)
      .then(bytes => {
        if (!active) {
          return
        }

        let total = 0
        const unzipped = unzipSync(bytes, {
          filter: file => {
            if (file.originalSize > MAX_ENTRY_BYTES) {
              return false
            }

            total += file.originalSize

            return total <= MAX_TOTAL_BYTES
          }
        })

        const list = Object.entries(unzipped)
          .map(([name, data]) => ({ data, name, size: data.length }))
          .sort((left, right) => left.name.localeCompare(right.name))

        if (active) {
          setEntries(list)
          setSelected(list[0]?.name ?? '')
        }
      })
      .catch(() => {
        if (active) {
          setFailed(true)
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })

    return () => {
      active = false
    }
  }, [change, path])

  const activeEntry = useMemo(
    () => entries.find(entry => entry.name === selected) ?? null,
    [entries, selected]
  )

  const open = () => openExternalLink(mediaExternalUrl(filePathFromMediaPath(path)))
  const name = mediaName(path)

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-(--ui-bg-primary)">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-(--ui-stroke-tertiary) px-2">
        <span className="min-w-0 flex-1 truncate text-[0.66rem] text-(--ui-text-secondary)">{name}</span>
        {entries.length ? (
          <span className="shrink-0 text-[0.6rem] tabular-nums text-muted-foreground">{entries.length}</span>
        ) : null}
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

      <div className="flex min-h-0 flex-1">
        <div className="w-52 shrink-0 overflow-auto border-r border-(--ui-stroke-tertiary) py-1">
          {entries.map(entry => (
            <button
              className={cn(
                'flex w-full items-center gap-1.5 px-2 py-1 text-left text-[0.66rem] transition-colors hover:bg-(--ui-row-hover-background)',
                entry.name === selected && 'bg-(--ui-row-hover-background) text-foreground'
              )}
              key={entry.name}
              onClick={() => setSelected(entry.name)}
              type="button"
            >
              <Codicon name="file" size="0.7rem" />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              <span className="shrink-0 text-[0.58rem] tabular-nums text-muted-foreground">
                {formatBytes(entry.size)}
              </span>
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {loading ? (
            <PageLoader label={labels.loading} />
          ) : failed ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
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
          ) : !entries.length ? (
            <div className="flex h-full items-center justify-center text-[0.72rem] text-muted-foreground">
              {labels.archiveEmpty}
            </div>
          ) : !activeEntry ? (
            <div className="flex h-full items-center justify-center text-[0.72rem] text-muted-foreground">
              {labels.unavailable}
            </div>
          ) : IMAGE_EXTENSIONS.has(extensionOf(activeEntry.name)) ? (
            <img
              alt={activeEntry.name}
              className="max-h-full max-w-full rounded bg-white object-contain"
              src={toDataUrl(mimeForImage(activeEntry.name), activeEntry.data)}
            />
          ) : TEXT_EXTENSIONS.has(extensionOf(activeEntry.name)) ? (
            <pre className="whitespace-pre-wrap break-words text-[0.7rem]">
              {strFromU8(activeEntry.data).slice(0, MAX_PREVIEW_BYTES)}
            </pre>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
              <p className="text-[0.75rem] font-medium text-foreground">{activeEntry.name}</p>
              <p className="text-[0.66rem] text-muted-foreground">{formatBytes(activeEntry.size)}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
